"""
URL configuration for ERP project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from authentication.refresh_token import AdminTokenRefreshView, CutoffTokenRefreshView
from authentication.views import apple_app_site_association
from whatsapp_integration.views import redirect_to_whatsapp


urlpatterns = [
    path("admin-dashboard/", admin.site.urls),
    path("api/token/refresh/", CutoffTokenRefreshView.as_view(), name="token_refresh"),
    path("api/admin-token/refresh/", AdminTokenRefreshView.as_view(), name="admin-token_refresh"),
    # Versioning
    path("api/v1/auth/", include("authentication.api.v1.urls")),
    path("api/v1/", include("authentication.api.admin_v1.urls")),
    path("api/v1/service/", include("services.api.admin_v1.urls")),
    path("api/v1/shop/", include("shop.api.admin_v1.urls")),
    path("api/v1/whatsapp/", include("whatsapp_integration.api.admin.urls")),

    # marketplace admin url
    path("api/v1/marketplace/admin/", include("marketplace.api.admin_v1.urls")),


    
    # V3
    path("api/v3/auth/", include("authentication.api.v3.urls")),
    path("api/v3/service/", include("services.api.v3.urls")),
    path("api/v3/shop/", include("shop.api.v3.urls")),
    path("api/v3/bookings/", include("booking_v2.api.v3.urls")),
    path("api/v3/expenses/", include("expenses_v2.api.v3.urls")),     
    path("api/v3/subscriptions/", include("subscriptions.api.admin_v3.urls")),
    path("api/v3/shop-subscriptions/", include("subscriptions.api.v3.urls")),
    path("api/v3/notifications/", include("notifications.api.v3.urls")),
    path("api/v3/sales/", include("sales.api.v3.urls")),
    path("api/v3/tailored/", include("tailored.api.v3.urls")),
    path("api/v3/pdf/", include("pdf_service.api.v3.urls")),
    path("api/v3/invoice-templates/", include("invoice_templates.api.v3.urls")),
    path("api/v3/purchases/", include("purchase.api.v3.urls")),
    path("api/v3/marketplace/", include("marketplace.api.v3.urls")),

    # Finance
    path("api/v3/finance/", include("finance.api.v3.urls")),

    # Trending products & business insights
    path("api/v3/trending-insights/", include("trending_insights.api.v3.urls")),
    # Admin
    path("api/v3/notifications/admin/", include("notifications.api.admin_v3.urls")),

    # V4
    path("api/v4/shop/", include("shop.api.v4.urls")),
    path("api/v4/expenses/", include("expenses_v2.api.v4.urls")),
    path("api/v4/bookings/", include("booking_v2.api.v4.urls")),
    path("api/v4/auth/", include("authentication.api.v4.urls")),
    path("api/v4/sales/", include("sales.api.v4.urls")),

    path('.well-known/apple-app-site-association', apple_app_site_association),
    path('apple-app-site-association', apple_app_site_association),    

    # V5
    path("api/v5/bookings/", include("booking_v2.api.v5.urls")),
    path("api/v5/expenses/", include("expenses_v2.api.v5.urls")),
    path("api/v5/sales/", include("sales.api.v5.urls")),
    path("api/v5/auth/", include("authentication.api.v5.urls")),

    # V5.1
    path("api/v5_1/bookings/", include("booking_v2.api.v5_1.urls")),

    path("call/<str:number>/", redirect_to_whatsapp, name="redirect_to_whatsapp"),

    # Public (unauthenticated) shop catalog APIs
    path("api/v3/public/", include("public_api.api.v3.urls")),

]

if settings.API_DOCS_ENABLED:
    from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView
    urlpatterns += [
        path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
        path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
        path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    ]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
