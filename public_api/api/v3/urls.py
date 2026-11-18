from django.urls import path
from public_api.api.v3.views.shop_info import PublicShopInfoView
from public_api.api.v3.views.shop_products import PublicShopProductsView

urlpatterns = [
    path("shops/<str:public_token>/info/", PublicShopInfoView.as_view(), name="public-shop-info"),
    path("shops/<str:public_token>/products/", PublicShopProductsView.as_view(), name="public-shop-products"),
]
