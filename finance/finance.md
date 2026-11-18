# 📘 Finance App — Technical Documentation

## 1. Overview

The **`finance`** app is the centralized financial layer of the system.
It is responsible for tracking:

* Money inflow and outflow
* Account-wise balances (cash, bank, etc.)
* Daily financial summaries (opening, closing, totals)
* A complete audit trail of all financial transactions

This app replaces heavy runtime aggregations with a **structured ledger-based system**.

---

## 2. Core Responsibilities

The finance app handles:

* ✅ Account management (Cash, Bank, Multiple Accounts)
* ✅ Transaction recording (LedgerEntry)
* ✅ Daily balance tracking (optional optimization)
* ✅ Financial reporting support
* ✅ Audit trail for all money movement

---

## 3. Core Models

---

## 3.1 Account Model

### Purpose:

Represents where money is stored (cash drawer, bank account, etc.)

```python
class Account(models.Model):
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='accounts')
    name = models.CharField(max_length=255)
    account_type = models.CharField(choices=[('cash','Cash'),('bank','Bank')], max_length=10)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
```

### Field Explanation:

| Field          | Description                                    |
| -------------- | ---------------------------------------------- |
| `shop`         | Owner shop                                     |
| `name`         | Display name (e.g. "Cash in Hand", "HDFC UPI") |
| `account_type` | Cash or Bank                                   |
| `is_default`   | Used for fallback mapping                      |
| `is_active`    | Allows disabling without deletion              |
| `created_at`   | Creation timestamp                             |

---

### Usage:

* Map payments to a physical account
* Support multiple bank accounts
* Separate cash vs digital money
* Enable account-wise reports

---

## 3.2 LedgerEntry Model (Core Table)

### Purpose:

Stores every financial transaction as a single row (central ledger system)

```python
class LedgerEntry(models.Model):
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='ledger_entries')
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='ledger_entries')

    amount = models.DecimalField(max_digits=12, decimal_places=2)
    signed_amount = models.DecimalField(max_digits=12, decimal_places=2)

    direction = models.CharField(choices=[('in','Inflow'),('out','Outflow')], max_length=10)

    transaction_type = models.CharField(max_length=50)

    payment_method = models.CharField(max_length=10)

    reference_id = models.IntegerField()
    reference_type = models.CharField(max_length=50)

    created_at = models.DateTimeField()

    notes = models.TextField(null=True, blank=True)

    created_by = models.ForeignKey(
        'auth.User',
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )

    is_system_generated = models.BooleanField(default=True)
```

---

### Field Explanation:

| Field                 | Description                 |
| --------------------- | --------------------------- |
| `account`             | Where money moved           |
| `amount`              | Absolute value              |
| `signed_amount`       | + for inflow, - for outflow |
| `direction`           | Inflow / Outflow            |
| `transaction_type`    | Type of transaction         |
| `payment_method`      | Cash / UPI / Bank           |
| `reference_id`        | ID of source object         |
| `reference_type`      | Source model name           |
| `created_at`          | System timestamp            |
| `notes`               | Optional description        |
| `created_by`          | User who created entry      |
| `is_system_generated` | Auto vs manual entry        |

---

### Transaction Types:

* `booking_payment`
* `sale_payment`
* `expense`
* `security_in`
* `security_out`
* `refund`
* `transfer_in`
* `transfer_out`

---

### Usage:

* Single source of truth for all financial data
* Replace multi-table aggregation
* Enable fast queries and reports
* Track every money movement

---

## 3.3 DailyBalance Model (Optional Optimization)

### Purpose:

Stores precomputed daily balances for fast queries

```python
class DailyBalance(models.Model):
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE)
    account = models.ForeignKey(Account, on_delete=models.CASCADE)

    date = models.DateField()

    opening_balance = models.DecimalField(max_digits=12, decimal_places=2)
    closing_balance = models.DecimalField(max_digits=12, decimal_places=2)

    total_in = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_out = models.DecimalField(max_digits=12, decimal_places=2, default=0)
```

---

### Usage:

* Fast dashboard loading
* Avoid recalculating historical balances
* Used for large-scale data optimization

---

## 4. Core Functions / Logic

---

## 4.1 Create Ledger Entry

```python
def create_ledger_entry(
    shop,
    account,
    amount,
    direction,
    transaction_type,
    payment_method,
    reference_id,
    reference_type,
    created_at,
    user=None,
    notes=None
):
    signed_amount = amount if direction == 'in' else -amount

    return LedgerEntry.objects.create(
        shop=shop,
        account=account,
        amount=amount,
        signed_amount=signed_amount,
        direction=direction,
        transaction_type=transaction_type,
        payment_method=payment_method,
        reference_id=reference_id,
        reference_type=reference_type,
        effective_date=created_at.date(),
        created_at=created_at,
        created_by=user,
        notes=notes
    )
```

---

## 4.2 Get Daily Summary

```python
def get_daily_summary(shop):
    from django.db.models import Sum

    return (
        LedgerEntry.objects
        .filter(shop=shop)
        .values('effective_date')
        .annotate(
            total_in=Sum('amount', filter=Q(direction='in')),
            total_out=Sum('amount', filter=Q(direction='out')),
            net=Sum('signed_amount')
        )
        .order_by('effective_date')
    )
```

---

## 4.3 Running Balance Calculation

```python
def calculate_running_balance(daily_data):
    balance = 0
    results = []

    for day in daily_data:
        opening = balance
        balance += day['net']
        closing = balance

        results.append({
            'date': day['effective_date'],
            'opening_balance': opening,
            'closing_balance': closing,
            'total_in': day['total_in'],
            'total_out': day['total_out'],
        })

    return results
```

---

## 4.4 Account Balance

```python
def get_account_balance(account):
    from django.db.models import Sum

    return (
        LedgerEntry.objects
        .filter(account=account)
        .aggregate(balance=Sum('signed_amount'))['balance'] or 0
    )
```

---

## 5. Data Flow

---

### When a Payment Happens:

```
Payment Created
    ↓
Account Resolved
    ↓
LedgerEntry Created
    ↓
Used in Reports
```

---

### When an Expense Happens:

```
Expense Created
    ↓
LedgerEntry (OUTFLOW)
    ↓
Account Balance Reduced
```

---

## 6. Benefits of This Architecture

* ✅ No complex joins across multiple tables
* ✅ Fast financial queries
* ✅ Clear audit trail
* ✅ Scalable design
* ✅ Easy reporting and analytics
* ✅ Supports multiple accounts

---

## 7. Future Extensions

* Account transfers (cash → bank)
* Profit & Loss reports
* GST reporting
* Daily closing system
* Role-based financial permissions
* Financial exports (PDF/Excel)

---

## 8. Summary

The **finance app** becomes the **single source of truth for all financial data** in the system.

It transforms:

* ❌ Complex aggregation system
  into
* ✅ Clean, scalable ledger-based architecture

---
