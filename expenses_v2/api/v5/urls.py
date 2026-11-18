from django.urls import path

from expenses_v2.api.v5.views.shop_expense_grouped import ShopExpenseGroupedAPIViewV5

urlpatterns = [
    path("wallet/expenses/", ShopExpenseGroupedAPIViewV5.as_view(), name="v5-wallet-expenses-grouped"),
]
