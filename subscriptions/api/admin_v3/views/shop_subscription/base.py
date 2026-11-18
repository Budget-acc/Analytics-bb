from rest_framework import viewsets
from authentication.permissions import IsAdminUserOnly
from django.http import Http404
from subscriptions.models import ShopSubscription
from subscriptions.api.admin_v3.serializers import ShopSubscriptionSerializer
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication


class ShopSubscriptionViewSet(viewsets.ViewSet):
    queryset = ShopSubscription.objects.all()
    serializer_class = ShopSubscriptionSerializer
    permission_classes = [IsAdminUserOnly]
    authentication_classes = [CutoffJWTAuthentication]

    def get_object(self):
        """
        Helper method to get object by primary key
        """
        pk = self.kwargs.get('pk')
        try:
            return ShopSubscription.objects.get(pk=pk)
        except ShopSubscription.DoesNotExist:
            raise Http404("ShopSubscription not found")
