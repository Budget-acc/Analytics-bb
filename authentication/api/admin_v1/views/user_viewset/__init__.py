from .base import UserViewSet
from .list import list
from .retrieve import retrieve
from .create import create
from .partial_update import partial_update
from .destroy import destroy
from .reset_password import reset_password

UserViewSet.list = list
UserViewSet.retrieve = retrieve
UserViewSet.create = create
UserViewSet.partial_update = partial_update
UserViewSet.destroy = destroy
UserViewSet.reset_password = reset_password

__all__ = ['UserViewSet']
