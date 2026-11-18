from .base import BugReportAdminViewSet
from .list import list
from .retrieve import retrieve
from .partial_update import partial_update

BugReportAdminViewSet.list = list
BugReportAdminViewSet.retrieve = retrieve
BugReportAdminViewSet.partial_update = partial_update
