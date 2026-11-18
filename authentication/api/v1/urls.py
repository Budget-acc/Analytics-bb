from django.urls import path
from .views import (
    UserAuthentication,
    UserProfileView,
    ChangePasswordAPIView,
    WalletAuthenticationView,
    UpdateSecondaryPasswordView,
)

urlpatterns = [
    path("login/", UserAuthentication.as_view()),
    path("profile/", UserProfileView.as_view()),
    path("change-password/", ChangePasswordAPIView.as_view()),
    path("wallet-login/", WalletAuthenticationView.as_view()),
    path("update-secondary-password/", UpdateSecondaryPasswordView.as_view(), name="update-secondary-password"),
]
