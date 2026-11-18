from django.utils.timezone import localtime


def compute_ledger_entries(payments, refunds, item_id, invoice_id, client_name, item_type):
    """
    payments: list of {amount, method, created_at, ...extra}, sorted ascending.
    refunds:  list of {amount, created_at}, sorted ascending.
    Returns list of ledger entry dicts (each includes sort_datetime).
    Extra keys in a payment dict (e.g. account_id) are passed through to settlement/raw entries.
    """
    if not payments:
        return []

    standard_keys = {"amount", "method", "created_at"}

    def make_entry(dt_aware, amount, method, source_payment=None):
        local_dt = localtime(dt_aware)
        entry = {
            "id": item_id,
            "invoice_id": invoice_id,
            "client_name": client_name,
            "payment_amount": amount,
            "payment_method": method,
            "created_at": local_dt.strftime("%d-%m-%Y %H:%M:%S"),
            "type": item_type,
            "sort_datetime": local_dt,
        }
        if source_payment:
            entry.update({k: v for k, v in source_payment.items() if k not in standard_keys})
        return entry

    if not refunds:
        return [make_entry(p["created_at"], p["amount"], p["method"], source_payment=p) for p in payments]

    latest_refund_time = refunds[-1]["created_at"]
    latest_payment_time = payments[-1]["created_at"]
    entries = []

    if latest_refund_time >= latest_payment_time:
        net = sum(p["amount"] for p in payments) - sum(r["amount"] for r in refunds)
        if net > 0:
            entries.append(make_entry(latest_refund_time, net, payments[-1]["method"], source_payment=payments[-1]))
    else:
        payments_before = [p for p in payments if p["created_at"] <= latest_refund_time]
        payments_after = [p for p in payments if p["created_at"] > latest_refund_time]
        if payments_before:
            net = sum(p["amount"] for p in payments_before) - sum(r["amount"] for r in refunds)
            if net > 0:
                entries.append(make_entry(latest_refund_time, net, payments_before[-1]["method"], source_payment=payments_before[-1]))
        for payment in payments_after:
            entries.append(make_entry(payment["created_at"], payment["amount"], payment["method"], source_payment=payment))

    return entries
