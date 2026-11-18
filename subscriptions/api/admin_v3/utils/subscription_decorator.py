from functools import wraps
from django.shortcuts import redirect
from django.http import HttpResponseForbidden
from django.urls import reverse

from subscriptions.models import has_feature_access


def feature_required(feature_code):
    """
    Decorator to check if the current shop user has access to a feature

    Usage:
        @feature_required('ADVANCED_BOOKING')
        def view_function(request, *args, **kwargs):
            # Only accessible if user has the feature
            ...
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(request, *args, **kwargs):
            # Check if user is authenticated and has a shop user
            if not request.user.is_authenticated:
                return redirect(reverse('login'))

            # Get current shop user (this depends on your app's context)
            shop_user = getattr(request, 'shop_user', None)

            if not shop_user:
                return HttpResponseForbidden("You don't have access to this shop")

            # Check if user has access to the required feature
            if not has_feature_access(shop_user, feature_code):
                return HttpResponseForbidden("Your subscription doesn't include this feature")

            # User has access, proceed with the view
            return view_func(request, *args, **kwargs)

        return wrapped_view

    return decorator
