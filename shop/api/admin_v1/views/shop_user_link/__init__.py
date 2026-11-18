from .base import ShopUserLinkAPIView
from .post import post
from .get import get
from .patch import patch
from .delete import delete

ShopUserLinkAPIView.post = post
ShopUserLinkAPIView.get = get
ShopUserLinkAPIView.patch = patch
ShopUserLinkAPIView.delete = delete
