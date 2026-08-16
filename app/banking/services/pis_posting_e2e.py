from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from django.conf import settings
from django.contrib.contenttypes.models import ContentType

from accounting.models import JournalEntry
from banking.provider_models import PaymentOrder
from banking.services.payment_order_lifecycle import PaymentOrderLifecycle
from events.dispatcher import publish


@dataclass
class PisPostingE2eCheck:
    name: str
    passed: bool
    detail: str = ''


@dataclass
class PisPostingE2eReport:
    checks: list[PisPostingE2eCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def add(self, name: str, passed: bool, detail: str = '') -> None:
        self.checks.append(PisPostingE2eCheck(name=name, passed=passed, detail=detail))


def resolve_posting_e2e_order(
    *,
    order_id: int | None = None,
    tenant_slug: str | None = None,
) -> PaymentOrder:
    qs = PaymentOrder.all_objects.select_related(
        'connection__bank_provider',
        'payment',
        'payment__related_invoice',
    ).filter(
        payment__isnull=False,
        status__in=('authorised', 'executed'),
    )

    if order_id is not None:
        return qs.get(pk=order_id)

    if tenant_slug:
        order = qs.filter(tenant__slug=tenant_slug).order_by('-updated_at').first()
        if order is None:
            raise PaymentOrder.DoesNotExist(
                f'Nema authorised/executed naloga s payment FK za {tenant_slug}.',
            )
        return order

    order = qs.order_by('-updated_at').first()
    if order is None:
        raise PaymentOrder.DoesNotExist(
            'Nema authorised/executed PaymentOrder naloga s povezanim Payment zapisom.',
        )
    return order


def run_payment_posting_e2e(order: PaymentOrder) -> PisPostingE2eReport:
    report = PisPostingE2eReport()

    if order.payment_id is None:
        report.add('payment_linked', False, 'PaymentOrder nema payment FK.')
        return report
    report.add('payment_linked', True, f'payment #{order.payment_id}')

    provider = order.connection.bank_provider
    if order.status == 'authorised':
        flag_enabled = getattr(settings, 'PIS_SANDBOX_ALLOW_POSTING_ON_AUTHORISED', False)
        sandbox_ok = provider.environment == 'sandbox' and flag_enabled
        report.add(
            'authorised_posting_allowed',
            sandbox_ok,
            (
                f'status=authorised, environment={provider.environment}, '
                f'PIS_SANDBOX_ALLOW_POSTING_ON_AUTHORISED={flag_enabled}'
            ),
        )
        if not sandbox_ok:
            return report
    else:
        report.add('order_status', True, order.status)

    event = PaymentOrderLifecycle._build_payment_executed_event(order)
    payment = order.payment
    payment_ct = ContentType.objects.get_for_model(payment)
    payment_journal_qs = JournalEntry.all_objects.filter(
        source_content_type=payment_ct,
        source_object_id=payment.pk,
    )
    journal_count_before = payment_journal_qs.count()

    publish(event)
    order.refresh_from_db()

    report.add(
        'first_posting_journal_entry',
        order.posting_journal_entry_id is not None,
        (
            f'entry #{order.posting_journal_entry_id}'
            if order.posting_journal_entry_id
            else 'posting_journal_entry je NULL'
        ),
    )
    report.add(
        'first_posting_posted_at',
        order.posted_at is not None,
        str(order.posted_at) if order.posted_at else 'posted_at je NULL',
    )
    journal_count_after_first = payment_journal_qs.count()
    report.add(
        'first_posting_journal_count',
        journal_count_after_first > journal_count_before or order.posting_journal_entry_id is not None,
        f'{journal_count_before} → {journal_count_after_first}',
    )

    posted_at_after_first = order.posted_at
    entry_id_after_first = order.posting_journal_entry_id

    publish(event)
    order.refresh_from_db()

    journal_count_after_second = payment_journal_qs.count()
    report.add(
        'idempotent_journal_count',
        journal_count_after_second == journal_count_after_first,
        f'count={journal_count_after_second}',
    )
    report.add(
        'idempotent_posted_at',
        order.posted_at == posted_at_after_first,
        f'posted_at={order.posted_at}',
    )
    report.add(
        'idempotent_journal_entry',
        order.posting_journal_entry_id == entry_id_after_first,
        f'entry #{order.posting_journal_entry_id}',
    )

    return report
