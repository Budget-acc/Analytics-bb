from django.shortcuts import redirect
from django.urls import reverse
from django.contrib import messages

class SubscriptionCheckMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Code to be executed for each request before
        # the view (and later middleware) are called.

        # Check if user is authenticated and accessing shop views
        user = request.user
        path = request.path

        if user.is_authenticated and path.startswith('/shop/') and not path.startswith('/shop/subscribe'):
            # Get current shop (this depends on your app's context)
            shop = getattr(request, 'shop', None)

            if shop and (not hasattr(shop, 'subscription') or not shop.subscription.is_active()):
                messages.error(request, "Your shop's subscription has expired or is inactive.")
                return redirect(reverse('subscription:plans'))

        response = self.get_response(request)
        return response
