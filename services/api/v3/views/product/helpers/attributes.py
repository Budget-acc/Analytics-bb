import logging
from decimal import Decimal, InvalidOperation

logger = logging.getLogger("api_requests")


def _validate_and_save_product_attributes(self, general_service, attributes_data, product=None):
    """
    Validate and prepare service attribute values for a product.

    Args:
        general_service: GeneralService instance
        attributes_data: Dict of attribute_key: value pairs from request
        product: Product instance (None for create, instance for update)

    Returns:
        tuple: (is_valid, errors, validated_attributes)
        - is_valid: Boolean
        - errors: Dict of errors
        - validated_attributes: List of dicts ready for ServiceAttributeValue creation

    Raises:
        None - returns validation errors in tuple
    """
    from services.models import ServiceAttributeTemplate, ServiceAttributeValue
    from datetime import datetime
    from decimal import Decimal, InvalidOperation

    if not attributes_data or not isinstance(attributes_data, dict):
        return True, {}, []

    errors = {}
    validated_attributes = []

    # Fetch all templates for this general service (product-level only)
    # templates = ServiceAttributeTemplate.objects.filter(
    #     general_service=general_service,
    #     applies_to='product'
    # ).select_related('general_service').select_for_update()
    provided_keys = set(attributes_data.keys())
    templates = ServiceAttributeTemplate.objects.filter(
        general_service=general_service,
        applies_to='product',
        attribute_key__in=provided_keys
    ).only(
        "id", "attribute_key", "attribute_label",
        "attribute_type", "choices", "is_required"
    )

    # Create a lookup dict for quick access
    templates_dict = {template.attribute_key: template for template in templates}

    # Check for unknown attributes
    for attr_key in attributes_data.keys():
        if attr_key not in templates_dict:
            errors[attr_key] = [
                f"Attribute '{attr_key}' is not valid for service '{general_service.name}'. "
                f"Valid attributes: {', '.join(templates_dict.keys())}"
            ]

    # Validate each template
    # for template in templates:
    for template in templates_dict.values():
        attr_key = template.attribute_key
        attr_value = attributes_data.get(attr_key)

        # Check required fields
        if template.is_required and (attr_value is None or attr_value == ""):
            errors[attr_key] = [f"'{template.attribute_label}' is required."]
            continue

        # Skip validation if not required and value is empty
        if attr_value is None or attr_value == "":
            continue

        # Type-specific validation
        validated_value = None

        try:
            if template.attribute_type == 'text':
                if not isinstance(attr_value, str):
                    errors[attr_key] = [f"'{template.attribute_label}' must be text."]
                    continue
                validated_value = attr_value.strip()

            elif template.attribute_type == 'number':
                try:
                    validated_value = int(attr_value)
                except (ValueError, TypeError):
                    errors[attr_key] = [f"'{template.attribute_label}' must be a whole number."]
                    continue

            elif template.attribute_type == 'decimal':
                try:
                    validated_value = float(Decimal(str(attr_value)))
                except (InvalidOperation, ValueError, TypeError):
                    errors[attr_key] = [f"'{template.attribute_label}' must be a decimal number."]
                    continue

            elif template.attribute_type == 'boolean':
                if isinstance(attr_value, bool):
                    validated_value = attr_value
                elif isinstance(attr_value, str):
                    if attr_value.lower() in ['true', '1', 'yes']:
                        validated_value = True
                    elif attr_value.lower() in ['false', '0', 'no']:
                        validated_value = False
                    else:
                        errors[attr_key] = [f"'{template.attribute_label}' must be true or false."]
                        continue
                else:
                    errors[attr_key] = [f"'{template.attribute_label}' must be true or false."]
                    continue

            elif template.attribute_type == 'choice':
                if not template.choices:
                    errors[attr_key] = [f"No choices defined for '{template.attribute_label}'."]
                    continue

                # choices should be a list like ["option1", "option2", "option3"]
                if attr_value not in template.choices:
                    errors[attr_key] = [
                        f"'{attr_value}' is not a valid choice for '{template.attribute_label}'. "
                        f"Valid choices: {', '.join(template.choices)}"
                    ]
                    continue
                validated_value = attr_value

            elif template.attribute_type == 'date':
                # Support multiple date formats
                date_formats = ['%Y-%m-%d', '%d-%m-%Y', '%m/%d/%Y', '%d/%m/%Y']
                parsed_date = None

                for fmt in date_formats:
                    try:
                        parsed_date = datetime.strptime(str(attr_value), fmt)
                        break
                    except ValueError:
                        continue

                if not parsed_date:
                    errors[attr_key] = [
                        f"'{template.attribute_label}' must be a valid date (YYYY-MM-DD format recommended)."
                    ]
                    continue

                validated_value = parsed_date.strftime('%Y-%m-%d')

            # Add to validated list
            validated_attributes.append({
                'template': template,
                'value': validated_value
            })

        except Exception as e:
            logger.error(f"Unexpected error validating attribute '{attr_key}': {str(e)}", exc_info=True)
            errors[attr_key] = [f"Invalid value for '{template.attribute_label}'."]

    is_valid = len(errors) == 0
    return is_valid, errors, validated_attributes


def _save_product_attributes(self, product, validated_attributes, is_partial_update=False, is_new=False):
    """
    Save validated attributes to database.

    Args:
        product: Product instance
        validated_attributes: List of dicts with 'template' and 'value' keys
        is_partial_update: If True, only update specified attributes
    """
    from services.models import ServiceAttributeValue

    if is_partial_update:
        # PARTIAL UPDATE: Only update/create the attributes that were provided
        for attr_data in validated_attributes:
            template = attr_data['template']
            value = attr_data['value']

            # Update or create the specific attribute
            ServiceAttributeValue.objects.update_or_create(
                product=product,
                attribute=template,
                defaults={'value': value}
            )

            logger.info(f"[Attributes] Updated/Created attribute '{template.attribute_key}' for product {product.id}")
    else:
        # FULL UPDATE/CREATE: for new products skip the DELETE (nothing exists yet)
        if not is_new:
            ServiceAttributeValue.objects.filter(product=product).delete()

        # Create new attribute values
        attribute_values = []
        for attr_data in validated_attributes:
            attribute_values.append(
                ServiceAttributeValue(
                    product=product,
                    attribute=attr_data['template'],
                    value=attr_data['value']
                )
            )

        if attribute_values:
            ServiceAttributeValue.objects.bulk_create(attribute_values)
