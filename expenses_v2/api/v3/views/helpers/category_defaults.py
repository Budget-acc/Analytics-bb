"""Shared default expense/income category definitions for expenses_v2."""

EXPENSE_CATEGORY_NAMES = [
    "Electricity bill",
    "Salary",
    "Food",
    "Maintenance",
]
INCOME_CATEGORY_NAMES = [
    "Deposit",
    "Commission",
    "Repay",
]
INCOME_AND_EXPENSE_CATEGORY_NAMES = [
    "Miscellaneous",
    "Opening Balance",
]

CATEGORY_DEFINITIONS = {
    "expense": EXPENSE_CATEGORY_NAMES,
    "income": INCOME_CATEGORY_NAMES,
    "income_and_expense": INCOME_AND_EXPENSE_CATEGORY_NAMES,
}
