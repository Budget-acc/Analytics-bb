from .base import TailorViewSet
from .list import list
from .retrieve import retrieve
from .create import create
from .partial_update import partial_update
from .destroy import destroy
from .tailor_monthly_summary import tailor_monthly_summary

TailorViewSet.list = list
TailorViewSet.retrieve = retrieve
TailorViewSet.create = create
TailorViewSet.partial_update = partial_update
TailorViewSet.destroy = destroy
TailorViewSet.tailor_monthly_summary = tailor_monthly_summary

__all__ = ['TailorViewSet']
