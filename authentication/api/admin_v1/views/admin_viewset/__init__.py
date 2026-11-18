from .base import AdminViewSet
from .post import change_password

AdminViewSet.change_password = change_password

__all__ = ['AdminViewSet']
