from finance.api.v3.views.account_viewset.base import AccountViewSetBase
from finance.api.v3.views.account_viewset.list import AccountListMixin
from finance.api.v3.views.account_viewset.list_with_amounts import AccountListWithAmountsMixin
from finance.api.v3.views.account_viewset.retrieve import AccountRetrieveMixin
from finance.api.v3.views.account_viewset.create import AccountCreateMixin
from finance.api.v3.views.account_viewset.partial_update import AccountPartialUpdateMixin
from finance.api.v3.views.account_viewset.destroy import AccountDestroyMixin


class AccountViewSet(
    AccountListMixin,
    AccountListWithAmountsMixin,
    AccountRetrieveMixin,
    AccountCreateMixin,
    AccountPartialUpdateMixin,
    AccountDestroyMixin,
    AccountViewSetBase,
):
    """
    Account ViewSet — finance/api/v3/

    Endpoints:
        GET    /finance/v3/accounts/              list            — all roles
        GET    /finance/v3/accounts/with-amounts/ list_with_amounts — all roles
        POST   /finance/v3/accounts/              create          — OWNER only
        GET    /finance/v3/accounts/{id}/         retrieve        — all roles
        PATCH  /finance/v3/accounts/{id}/         partial_update  — OWNER only
        DELETE /finance/v3/accounts/{id}/         destroy         — OWNER only

    Notes:
        - Default accounts (is_default=True) cannot be renamed, change type, or deleted.
        - Accounts with LedgerEntry records cannot be deleted (financial history guard).
        - Race-condition-safe: DB unique constraint on (shop, name) catches concurrent creates.
        - All queries scoped to shop via self.shop_id resolved in initial().
        - list_with_amounts endpoint includes current balances calculated from ledger entries.
    """
    pass
