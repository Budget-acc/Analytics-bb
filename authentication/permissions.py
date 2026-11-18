from rest_framework.permissions import BasePermission, SAFE_METHODS

ADMIN_ROLE = "ADMIN"
VENDOR_ADMIN_ROLE = "VENDOR_ADMIN"

# Roles allowed to log in through the admin-side login (auth/admin/login/).
ADMIN_PANEL_ROLES = (ADMIN_ROLE, VENDOR_ADMIN_ROLE)


def _is_admin_panel_user(user):
    return (
        user.is_authenticated
        and user.is_staff
        and user.role in ADMIN_PANEL_ROLES
    )


class IsAdminUserOnly(BasePermission):
    """
    Allows access only to users with role 'ADMIN' and who are also staff or superuser.
    """

    def has_permission(self, request, view):
        user = request.user
        return user.is_authenticated and user.role == ADMIN_ROLE and user.is_staff


class HasMarketplaceAccess(BasePermission):
    """
    Marketplace/Vendor admin section access: ADMIN and approved (is_staff)
    VENDOR_ADMIN users.
    """

    def has_permission(self, request, view):
        return _is_admin_panel_user(request.user)


class HasMarketplaceCatalogAccess(BasePermission):
    """
    Shared marketplace catalog (categories, subcategories, fabrics):
    ADMIN has full access; approved VENDOR_ADMIN is read-only, so a vendor
    can pick catalog entries for their products but can't change data every
    other vendor depends on.
    """

    def has_permission(self, request, view):
        user = request.user
        if not _is_admin_panel_user(user):
            return False
        return user.role == ADMIN_ROLE or request.method in SAFE_METHODS


class IsAdminPanelUser(BasePermission):
    """
    Any user who can log in to the admin panel (ADMIN or approved VENDOR_ADMIN).
    Used for self-service endpoints such as change-password.
    """

    def has_permission(self, request, view):
        return _is_admin_panel_user(request.user)
