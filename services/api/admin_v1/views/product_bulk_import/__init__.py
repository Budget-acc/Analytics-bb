from .base import ProductBulkImportViewSet
from .create import create
from .status import status
from .cancel import cancel

ProductBulkImportViewSet.create = create
ProductBulkImportViewSet.status = status
ProductBulkImportViewSet.cancel = cancel

__all__ = ['ProductBulkImportViewSet']
