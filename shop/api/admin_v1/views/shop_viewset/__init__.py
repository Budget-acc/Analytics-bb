from .base import ShopViewSet
from .list import list
from .retrieve import retrieve
from .create import create
from .partial_update import partial_update
from .destroy import destroy
from .generate_report import generate_report
from .idle_days_report import idle_days_report

ShopViewSet.list = list
ShopViewSet.retrieve = retrieve
ShopViewSet.create = create
ShopViewSet.partial_update = partial_update
ShopViewSet.destroy = destroy
ShopViewSet.generate_report = generate_report
ShopViewSet.idle_days_report = idle_days_report
