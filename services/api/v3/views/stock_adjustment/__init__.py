from .base import ProductStockAdjustmentViewSet
from .create import create
from .list import list
from .summary import summary

ProductStockAdjustmentViewSet.create = create
ProductStockAdjustmentViewSet.list = list
ProductStockAdjustmentViewSet.summary = summary

__all__ = ["ProductStockAdjustmentViewSet"]
