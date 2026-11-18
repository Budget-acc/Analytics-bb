from .base import ShopWhatsAppManagementViewSet
from .retrieve import retrieve_by_shop
from .create import create
from .partial_update import partial_update
from .destroy import destroy
from .create_template import create_template
from .update_template import update_template
from .destroy_template import destroy_template

ShopWhatsAppManagementViewSet.retrieve_by_shop = retrieve_by_shop
ShopWhatsAppManagementViewSet.create = create
ShopWhatsAppManagementViewSet.partial_update = partial_update
ShopWhatsAppManagementViewSet.destroy = destroy
ShopWhatsAppManagementViewSet.create_template = create_template
ShopWhatsAppManagementViewSet.update_template = update_template
ShopWhatsAppManagementViewSet.destroy_template = destroy_template
