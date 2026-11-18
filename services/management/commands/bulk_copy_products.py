"""
Management command: bulk_copy_products

PURPOSE
-------
Duplicates all products (and everything hanging off them) that belong to a
source "shop service" (a ``ShopOwnerService`` row, i.e. a shop + the general
service it offers) into a destination "shop service". The source data is
never touched/moved - this is a COPY, not a MOVE.

For every non-deleted ``Product`` under the source shop service, the command
copies:

* The product itself (all fields except the shop-service link, which is
  re-pointed at the destination shop service).
* Its non-deleted ``ProductVariant`` rows (re-pointed at the destination shop
  and the newly created product).
* ``ServiceAttributeTemplate`` rows used by the source's general service -
  these are copied (once, de-duplicated by ``(attribute_key, applies_to)``)
  into the destination general service if an equivalent template does not
  already exist there.
* ``ServiceAttributeValue`` rows attached to the product and to its variants,
  re-pointed at the copied product/variants and the (copied or already
  existing) destination attribute templates.

This single command covers both use cases described by the business
requirement:

1. **Shop -> shop copy**: source and destination shop services belong to
   different shops (typical "clone shop's catalog into a new shop" use
   case).
2. **Service -> service migration**: source and destination shop services
   can also belong to the *same* shop, letting you move/copy a shop's
   products from one of its services to another.

Both cases use the exact same code path - the command only cares about the
source/destination ``ShopOwnerService`` ids; whether those two rows happen to
share a shop or not is irrelevant to the logic.

ARGUMENTS
---------
--source-shop-id       (int, required) Shop id the products are copied FROM.
                        Used only as a safety check against --source-service-id.
--source-service-id    (int, required) ShopOwnerService id (a shop's
                        instance of a general service) to copy products from.
--dest-shop-id         (int, required) Shop id the products are copied TO.
                        Used only as a safety check against --dest-service-id.
--dest-service-id      (int, required) ShopOwnerService id to copy products
                        into.
--batch-size           (int, optional, default 500) Batch size used for all
                        bulk_create() calls.
--dry-run              (flag, optional) Run the whole copy inside a
                        transaction and roll it back at the end, printing
                        what *would* have been created. Nothing is
                        persisted.

USAGE EXAMPLES
---------------
Copy all products from shop 189's service 748 into shop 191's service 750::

    python manage.py bulk_copy_products \\
        --source-shop-id 189 --source-service-id 748 \\
        --dest-shop-id 191 --dest-service-id 750

Move/copy products between two services owned by the SAME shop
(service-to-service migration)::

    python manage.py bulk_copy_products \\
        --source-shop-id 42 --source-service-id 101 \\
        --dest-shop-id 42 --dest-service-id 102

Dry run (no data is written, just a report)::

    python manage.py bulk_copy_products \\
        --source-shop-id 189 --source-service-id 748 \\
        --dest-shop-id 191 --dest-service-id 750 --dry-run

EXPECTED BEHAVIOUR
-------------------
* Only non-deleted products/variants are copied; soft-deleted rows are
  skipped.
* Copied products/variants get fresh primary keys - they are brand-new rows,
  fully independent of the source rows.
* ``ProductVariant.unique_code`` is globally unique, so if a source variant's
  ``unique_code`` already exists among non-deleted variants, the copy gets a
  ``"<code>_copy_<8 hex chars>"`` suffix so the insert doesn't collide.
* ``ProductVariant.external_qr_code`` is unique per shop, so the same
  collision-avoidance is applied, scoped to the destination shop.
* ``created_at`` / ``updated_at`` / ``structure_updated_at`` timestamps on
  the copies are preserved as they were on the copied product/variant.
* ``ServiceAttributeTemplate`` rows are only created for the destination
  general service when an equivalent one (same ``attribute_key`` +
  ``applies_to``) does not already exist there - existing destination
  templates are always reused, which also lets you migrate into a
  destination that already has (partially) matching templates.

LIMITATIONS / ASSUMPTIONS
--------------------------
* The command copies only what's listed above: products, variants, service
  attribute templates and values. Anything else that might reference a
  product (e.g. cart items, order line items, stock/booking history) is
  intentionally NOT copied - the destination products start with a clean
  slate.
* ``--source-shop-id``/``--dest-shop-id`` are used purely as a safety check
  (they must match the shop that owns the given ``--source-service-id`` /
  ``--dest-service-id``); the actual filtering is driven by the service ids.
* The command assumes a database backend whose ``bulk_create`` returns
  primary keys in the same order the objects were passed in (true for
  PostgreSQL, SQLite 3.35+, MariaDB 10.5+) - this project runs on
  PostgreSQL, so this holds.
* Not idempotent: running the command twice with the same arguments creates
  a second set of copies. There is no de-duplication against previously
  copied products.
"""

from uuid import uuid4

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from django.db.models import Max

from services.models import (
    Product,
    ProductVariant,
    ServiceAttributeTemplate,
    ServiceAttributeValue,
    ShopOwnerService,
)
from services.models.product import annotate_max_product_code_number
from shop.models.shop import Shop
from shop.models.shop_numbering_sequence import ShopNumberingSequence


class _DryRunRollback(Exception):
    """Internal sentinel used to unwind the dry-run transaction."""


class Command(BaseCommand):
    help = (
        "Bulk-copy all products (variants, service attribute templates and "
        "values) from one shop service to another. See module docstring for "
        "full documentation and usage examples."
    )

    def add_arguments(self, parser):
        parser.add_argument("--source-shop-id", type=int, required=True, help="Shop id to copy products FROM.")
        parser.add_argument("--source-service-id", type=int, required=True, help="ShopOwnerService id to copy products FROM.")
        parser.add_argument("--dest-shop-id", type=int, required=True, help="Shop id to copy products TO.")
        parser.add_argument("--dest-service-id", type=int, required=True, help="ShopOwnerService id to copy products TO.")
        parser.add_argument("--batch-size", type=int, default=500, help="Batch size for bulk_create() calls (default: 500).")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Run the copy inside a transaction and roll it back, printing what would have happened.",
        )

    def handle(self, *args, **options):
        source_shop_id = options["source_shop_id"]
        source_service_id = options["source_service_id"]
        dest_shop_id = options["dest_shop_id"]
        dest_service_id = options["dest_service_id"]
        batch_size = options["batch_size"]
        dry_run = options["dry_run"]

        source_shop = self._get_or_error(Shop, source_shop_id, "Source shop")
        dest_shop = self._get_or_error(Shop, dest_shop_id, "Destination shop")
        source_service = self._get_or_error(ShopOwnerService, source_service_id, "Source shop service")
        dest_service = self._get_or_error(ShopOwnerService, dest_service_id, "Destination shop service")

        if source_service.shop_id != source_shop.id:
            raise CommandError(
                f"--source-service-id {source_service_id} belongs to shop "
                f"{source_service.shop_id}, not --source-shop-id {source_shop_id}."
            )
        if dest_service.shop_id != dest_shop.id:
            raise CommandError(
                f"--dest-service-id {dest_service_id} belongs to shop "
                f"{dest_service.shop_id}, not --dest-shop-id {dest_shop_id}."
            )

        try:
            with transaction.atomic():
                stats = self._copy(source_service, dest_service, dest_shop, batch_size)
                if dry_run:
                    raise _DryRunRollback()
        except _DryRunRollback:
            self.stdout.write(self.style.WARNING("Dry run - transaction rolled back, nothing was persisted."))

        self.stdout.write(self.style.SUCCESS("Bulk product copy completed." if not dry_run else "Dry run completed."))
        self.stdout.write(f"Templates created: {stats['templates_created']}")
        self.stdout.write(f"Products copied:   {stats['products']}")
        self.stdout.write(f"Variants copied:   {stats['variants']}")
        self.stdout.write(f"Attribute values copied: {stats['attribute_values']}")

    @staticmethod
    def _get_or_error(model, pk, label):
        try:
            return model.objects.get(pk=pk)
        except model.DoesNotExist:
            raise CommandError(f"{label} with id {pk} does not exist.")

    def _copy(self, source_service, dest_service, dest_shop, batch_size):
        dest_general_service = dest_service.general_service

        # ------------------------------------------------------------------
        # 1) Source products, with variants and product-level attribute
        #    values prefetched in the same round trip (avoids N+1 later).
        # ------------------------------------------------------------------
        src_products = list(
            Product.objects.filter(shop_service=source_service, deleted__isnull=True)
            .prefetch_related("variants", "attribute_values")
        )

        if not src_products:
            self.stdout.write(self.style.WARNING("No products found for the given source shop service."))
            return {"templates_created": 0, "products": 0, "variants": 0, "attribute_values": 0}

        # ------------------------------------------------------------------
        # 2) Ensure destination attribute templates exist for every template
        #    used by the source general service (create missing ones once).
        # ------------------------------------------------------------------
        src_templates = list(ServiceAttributeTemplate.objects.filter(general_service_id=source_service.general_service_id))
        dest_templates = ServiceAttributeTemplate.objects.filter(general_service=dest_general_service)
        dest_map = {(t.attribute_key, t.applies_to): t for t in dest_templates}

        to_create_templates = [
            ServiceAttributeTemplate(
                general_service=dest_general_service,
                attribute_key=st.attribute_key,
                attribute_label=st.attribute_label,
                attribute_type=st.attribute_type,
                choices=st.choices,
                applies_to=st.applies_to,
                is_required=st.is_required,
                display_order=st.display_order,
            )
            for st in src_templates
            if (st.attribute_key, st.applies_to) not in dest_map
        ]

        if to_create_templates:
            ServiceAttributeTemplate.objects.bulk_create(to_create_templates, batch_size=batch_size)
            dest_templates = ServiceAttributeTemplate.objects.filter(general_service=dest_general_service)
            dest_map = {(t.attribute_key, t.applies_to): t for t in dest_templates}

        src_template_map = {
            st.id: dest_map[(st.attribute_key, st.applies_to)]
            for st in src_templates
            if (st.attribute_key, st.applies_to) in dest_map
        }

        # ------------------------------------------------------------------
        # 3) Bulk-create the copied products in one shot, relying on the DB
        #    returning the created rows (with pks) in input order.
        # ------------------------------------------------------------------
        product_objs = [
            Product(
                shop_service=dest_service,
                name=p.name,
                description=p.description,
                color=p.color,
                category=p.category,
                model=p.model,
                image=p.image,
                thumbnail_image=p.thumbnail_image,
                is_active=p.is_active,
                purchase_price=p.purchase_price,
                price=p.price,
                sale_price=p.sale_price,
                hourly_rental_price=p.hourly_rental_price,
                created_at=p.created_at,
                updated_at=p.updated_at,
            )
            for p in src_products
        ]
        new_products = Product.objects.bulk_create(product_objs, batch_size=batch_size)
        src_to_new_product = dict(zip((p.id for p in src_products), new_products))

        # bulk_create() never calls Product.save(), so it never runs the
        # product_code generation logic there - assign codes here instead,
        # locking the destination shop's ShopNumberingSequence row the same
        # way Product.save() does, so this can't race with an organic
        # product creation for the same shop. The prefix (e.g. "PRD") lives
        # solely on ShopNumberingSequence.product_code_prefix and is never
        # stored here - only the zero-padded numeric part is written to
        # Product.product_code.
        dest_sequence = (
            ShopNumberingSequence.objects.select_for_update()
            .filter(shop_id=dest_shop.id).first()
        )
        if dest_sequence is None:
            # Normally already created by Shop.save()'s
            # _ensure_product_code_prefix() hook - defensive fallback for a
            # shop that predates that hook and hasn't been re-saved since.
            dest_sequence = ShopNumberingSequence.objects.create(shop_id=dest_shop.id)
            dest_sequence = ShopNumberingSequence.objects.select_for_update().get(pk=dest_sequence.pk)

        if not dest_sequence.last_product_number:
            # Tolerates legacy-format product_code values (see
            # annotate_max_product_code_number) instead of silently
            # excluding them and restarting numbering from the wrong base.
            dest_sequence.last_product_number = (
                annotate_max_product_code_number(
                    Product.objects.filter(shop_service__shop_id=dest_shop.id)
                )
                .aggregate(max_code=Max("code_int"))["max_code"]
            ) or 0

        next_number = dest_sequence.last_product_number
        for product in new_products:
            next_number += 1
            product.product_code = f"{next_number:05d}"
        Product.objects.bulk_update(new_products, ["product_code"], batch_size=batch_size)

        dest_sequence.last_product_number = next_number
        dest_sequence.save(update_fields=["last_product_number"])

        # ------------------------------------------------------------------
        # 4) Gather all source variants up front (single query) and resolve
        #    unique_code / external_qr_code collisions in bulk instead of
        #    per-row queries.
        # ------------------------------------------------------------------
        src_variants_by_product = {p.id: [v for v in p.variants.all() if v.deleted is None] for p in src_products}
        all_src_variants = [v for variants in src_variants_by_product.values() for v in variants]

        candidate_codes = {v.unique_code for v in all_src_variants if v.unique_code}
        existing_codes = set(
            ProductVariant.objects.filter(unique_code__in=candidate_codes, deleted__isnull=True)
            .values_list("unique_code", flat=True)
        ) if candidate_codes else set()

        candidate_ext_codes = {v.external_qr_code for v in all_src_variants if v.external_qr_code}
        existing_ext_codes = set(
            ProductVariant.objects.filter(
                shop_id=dest_shop.id, external_qr_code__in=candidate_ext_codes, deleted__isnull=True
            ).values_list("external_qr_code", flat=True)
        ) if candidate_ext_codes else set()

        variant_objs = []
        for src_p in src_products:
            new_p = src_to_new_product[src_p.id]
            for sv in src_variants_by_product[src_p.id]:
                unique_code = sv.unique_code
                if unique_code and unique_code in existing_codes:
                    unique_code = f"{unique_code}_copy_{uuid4().hex[:8]}"

                ext_q = sv.external_qr_code
                if ext_q and ext_q in existing_ext_codes:
                    ext_q = f"{ext_q}_copy_{uuid4().hex[:8]}"

                variant_objs.append(
                    ProductVariant(
                        shop_id=dest_shop.id,
                        product=new_p,
                        attribute=sv.attribute,
                        price=sv.price,
                        stock=sv.stock,
                        fabric_length=sv.fabric_length,
                        is_active=sv.is_active,
                        unique_code=unique_code,
                        external_qr_code=ext_q,
                        structure_updated_at=sv.structure_updated_at,
                        quantity_updated_at=sv.quantity_updated_at,
                        created_at=sv.created_at,
                    )
                )

        new_variants = ProductVariant.objects.bulk_create(variant_objs, batch_size=batch_size) if variant_objs else []
        src_variant_id_to_new = dict(zip((v.id for v in all_src_variants), new_variants))

        # ------------------------------------------------------------------
        # 5) Copy attribute values. Product-level values come from the
        #    prefetch done in step 1; variant-level values are fetched in a
        #    single query for ALL source variants (no per-product query).
        # ------------------------------------------------------------------
        attr_val_objs = []

        for src_p in src_products:
            new_p = src_to_new_product[src_p.id]
            for sav in src_p.attribute_values.all():
                if sav.product_id is None:
                    continue
                dest_attr = src_template_map.get(sav.attribute_id)
                if not dest_attr:
                    continue
                attr_val_objs.append(ServiceAttributeValue(product=new_p, attribute=dest_attr, value=sav.value))

        if all_src_variants:
            variant_attr_values = ServiceAttributeValue.objects.filter(
                variant_id__in=[v.id for v in all_src_variants]
            )
            for sav in variant_attr_values:
                dest_attr = src_template_map.get(sav.attribute_id)
                new_variant = src_variant_id_to_new.get(sav.variant_id)
                if not dest_attr or not new_variant:
                    continue
                attr_val_objs.append(ServiceAttributeValue(variant=new_variant, attribute=dest_attr, value=sav.value))

        if attr_val_objs:
            ServiceAttributeValue.objects.bulk_create(attr_val_objs, batch_size=batch_size)

        return {
            "templates_created": len(to_create_templates),
            "products": len(new_products),
            "variants": len(new_variants),
            "attribute_values": len(attr_val_objs),
        }
