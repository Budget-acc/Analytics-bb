from .base import ShopActivityLogViewSet
from .helpers.filters import _apply_filters
from .helpers.processors import _process_logs
from .helpers.variant_codes import _fetch_variant_codes
from .helpers.log_builder import _build_log_item
from .list import list
from .restore import restore

ShopActivityLogViewSet._apply_filters = _apply_filters
ShopActivityLogViewSet._process_logs = _process_logs
ShopActivityLogViewSet._fetch_variant_codes = _fetch_variant_codes
ShopActivityLogViewSet._build_log_item = _build_log_item
ShopActivityLogViewSet.list = list
ShopActivityLogViewSet.restore = restore
