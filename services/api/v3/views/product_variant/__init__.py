from .base import ProductVariantViewSet
from .create import create
from .partial_update import partial_update
from .destroy import destroy
from .helpers.validators import _validate_external_qr_code

ProductVariantViewSet.create = create
ProductVariantViewSet.partial_update = partial_update
ProductVariantViewSet.destroy = destroy
ProductVariantViewSet._validate_external_qr_code = _validate_external_qr_code

__all__ = ["ProductVariantViewSet"]
