# /srv/finestar/erp/app/invoices/signals.py
from decimal import Decimal
from django.db import transaction
from django.db.models import Sum, F, DecimalField, ExpressionWrapper, Value
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from .models import Invoice, InvoiceItem


def _recalculate_invoice_totals(invoice_id: int) -> None:
    """
    Izračunaj subtotal, PDV i total iz svih povezanih stavki u bazi (agregacija).
    """
    items_qs = InvoiceItem.objects.filter(invoice_id=invoice_id)

    # subtotal = Σ (quantity * unit_price)
    money_expr = ExpressionWrapper(
        F("quantity") * F("unit_price"),
        output_field=DecimalField(max_digits=18, decimal_places=2),
    )
    subtotal = items_qs.aggregate(total=Sum(money_expr))["total"] or Decimal("0.00")

    # tax = Σ (quantity * unit_price * tax_rate / 100)
    tax_expr = ExpressionWrapper(
        F("quantity") * F("unit_price") * F("tax_rate") / Value(100),
        output_field=DecimalField(max_digits=18, decimal_places=2),
    )
    tax_amount = items_qs.aggregate(total=Sum(tax_expr))["total"] or Decimal("0.00")

    total_amount = subtotal + tax_amount

    # Direktni update bez poziva model.save() (brže i bez petlje signala)
    Invoice.objects.filter(id=invoice_id).update(
        subtotal=subtotal,
        tax_amount=tax_amount,
        total_amount=total_amount,
    )


@receiver(post_save, sender=InvoiceItem)
def invoiceitem_post_save(sender, instance: InvoiceItem, **kwargs):
    # Izračunaj nakon commit-a da inline snimanja u adminu budu konzistentna
    transaction.on_commit(lambda: _recalculate_invoice_totals(instance.invoice_id))


@receiver(post_delete, sender=InvoiceItem)
def invoiceitem_post_delete(sender, instance: InvoiceItem, **kwargs):
    transaction.on_commit(lambda: _recalculate_invoice_totals(instance.invoice_id))
