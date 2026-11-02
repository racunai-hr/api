"""AI prepoznaje događaj. Vozilo i pravila određuju profil. Korisnik potvrđuje."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import transaction

from accounting.models import PostingRule, Vehicle, VehicleAccountingRole
from accounting.services.foreign_vat import TREATMENTS
from accounting.services.fx import resolve_fx_rate
from accounting.services.posting import build_document_posting_plan
from domains.finance.ai.accounting_classify import PROFILES, build_prompt, classify_event, document_facts
from domains.finance.services.posting_memory import line_signature, load_remembered_lines, normalize_line_text
from domains.finance.services.vehicle_posting_amounts import passenger_split_ready
from expenses.models import (
    ACCOUNT_LOCKED_PROFILES,
    Expense,
    ExpenseCategory,
    ExpenseLine,
    ExpensePostingProfile,
    SupplierItemProfileMemory,
)

ROLE_TO_PROFILE = {
    'inventory_for_resale': ExpensePostingProfile.VEHICLE_INVENTORY_COST,
    'company_passenger': ExpensePostingProfile.COMPANY_PASSENGER_VEHICLE,
    'full_vat_business_use': ExpensePostingProfile.FULL_VAT_VEHICLE,
}
VEHICLE_PROFILES = frozenset(ROLE_TO_PROFILE.values()) | {ExpensePostingProfile.GOODS_INWARD_COST}
PROFILE_LABELS = {
    ExpensePostingProfile.VEHICLE_INVENTORY_COST: 'Nabavna vrijednost vozila na zalihi',
    ExpensePostingProfile.GOODS_INWARD_COST: 'Zavisni trošak nabave robe',
    ExpensePostingProfile.COMPANY_PASSENGER_VEHICLE: 'Gorivo službenog osobnog vozila',
    ExpensePostingProfile.FULL_VAT_VEHICLE: 'Vozilo s punim odbitkom pretporeza',
    ExpensePostingProfile.OPEX: 'Operativni trošak',
    ExpensePostingProfile.ASSET_PURCHASE: 'Nabava dugotrajne imovine',
}
INVENTORY_FIXED_ASSET_REASON = 'Vozilo za daljnju prodaju ne smije biti vezano uz osnovno sredstvo.'
INVENTORY_ASSET_REASON = 'Vozilo za daljnju prodaju ne može se knjižiti kao nabava imovine.'
INVENTORY_ROLE_REASON = 'Namjenu vozila za daljnju prodaju treba zapamtiti na vozilu.'
ROLE_OPTIONS = (
    ('inventory_for_resale', 'Vozilo za daljnju prodaju'),
    ('company_passenger', 'Službeno osobno vozilo'),
    ('full_vat_business_use', 'Vozilo s punim odbitkom'),
    ('other', 'Ostalo'),
)


class AccountingReviewBlocked(Exception):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def require_posting_review(result: dict) -> None:
    if result.get('status') == 'needs_information':
        raise AccountingReviewBlocked(result.get('reason') or 'Nedostaje podatak za knjiženje.')


def list_active_vehicles(tenant) -> list[dict]:
    rows = Vehicle.all_objects.filter(tenant=tenant, is_active=True).order_by('name', 'id')
    return [_vehicle_payload(row) for row in rows]


def review_expense(tenant, expense: Expense, answers: dict | None, user) -> dict:
    answers = _answers(answers)
    lines = [
        {'description': text, 'amount': ''}
        for text, _normalized in _expense_line_texts(expense)
    ]
    net = Decimal(expense.amount or 0) - Decimal(expense.tax_amount or 0)
    facts = _facts(
        tenant_id=tenant.id,
        supplier_id=expense.supplier_id,
        number=expense.receipt_number or expense.expense_number,
        document_date=expense.expense_date,
        net_amount=net,
        tax_amount=Decimal(expense.tax_amount or 0),
        gross_amount=Decimal(expense.amount or 0),
        currency=expense.currency or 'EUR',
        tax_category=getattr(expense, 'ubl_tax_category', '') or '',
        lines=lines,
        vehicle_id=expense.vehicle_id,
        exclude_expense_id=expense.pk,
    )
    _copy_foreign_facts(
        facts,
        net=expense.foreign_net_amount,
        vat=expense.foreign_vat_amount,
        gross=expense.foreign_gross_amount,
        country=expense.foreign_vat_country,
        vat_rate=expense.foreign_vat_rate,
    )
    return _review(tenant, facts, answers, user, expense=expense)


def review_invoice_import(tenant, run, answers: dict | None, user) -> dict:
    from domains.purchasing.services.dto import extracted_view
    from domains.purchasing.services.matching import parse_money

    answers = _answers(answers)
    view = extracted_view(run.extracted_payload or {})
    items = view.get('line_items') or []
    lines = []
    for item in items:
        if not isinstance(item, dict):
            continue
        description = str(item.get('description') or '').strip()
        if not description:
            continue
        lines.append({'description': description, 'amount': str(item.get('amount') or '')})
    gross = parse_money(view.get('total_amount')) or Decimal('0.00')
    tax = parse_money(view.get('tax_amount')) or Decimal('0.00')
    net = parse_money(view.get('net_amount'))
    if net is None:
        net = gross - tax
    facts = _facts(
        tenant_id=tenant.id,
        supplier_id=run.matched_partner_id,
        number=view.get('invoice_number') or '',
        document_date=_parse_date(view.get('issue_date')),
        net_amount=net,
        tax_amount=tax,
        gross_amount=gross,
        currency=view.get('currency') or 'EUR',
        tax_category=str((run.extracted_payload or {}).get('tax_category') or ''),
        lines=lines,
        vehicle_id=None,
        exclude_expense_id=None,
    )
    payload = run.extracted_payload or {}
    foreign_vat = parse_money(payload.get('foreign_vat_amount'))
    foreign_gross = parse_money(payload.get('foreign_gross_amount'))
    foreign_net = parse_money(payload.get('foreign_net_amount'))
    if foreign_gross is None and foreign_vat is not None:
        foreign_gross = gross
    if foreign_net is None and foreign_gross is not None and foreign_vat is not None:
        foreign_net = foreign_gross - foreign_vat
    _copy_foreign_facts(
        facts,
        net=foreign_net,
        vat=foreign_vat,
        gross=foreign_gross,
        country=str(payload.get('foreign_vat_country') or ''),
        vat_rate=parse_money(payload.get('foreign_vat_rate')),
    )
    return _review(tenant, facts, answers, user, expense=None, supplier=run.matched_partner)


def bind_review_choice(expense: Expense, result: dict, answers: dict | None) -> None:
    answers = _answers(answers)
    profile = result.get('posting_profile') or ''
    if profile in PROFILES:
        expense.posting_profile = profile
    vehicle = result.get('vehicle') or {}
    if vehicle.get('id'):
        expense.vehicle_id = vehicle['id']
    if profile in ACCOUNT_LOCKED_PROFILES:
        expense.expense_account = None
        expense.lines.all().update(posting_account=None)
    elif profile in VEHICLE_PROFILES:
        expense.expense_account = None
    _bind_foreign_vat(expense, result, answers)
    expense.save()
    role = answers.get('vehicle_accounting_role') or ''
    if answers.get('remember_vehicle_role') and expense.vehicle_id and role in dict(ROLE_OPTIONS):
        vehicle_row = Vehicle.all_objects.filter(
            tenant_id=expense.tenant_id,
            pk=expense.vehicle_id,
        ).first()
        if vehicle_row is not None:
            if role == VehicleAccountingRole.INVENTORY_FOR_RESALE and vehicle_row.fixed_asset_id:
                raise ValidationError({'vehicle': INVENTORY_FIXED_ASSET_REASON})
            vehicle_row.accounting_role = role
            vehicle_row.save(update_fields=['accounting_role', 'updated_at'])


def _review(tenant, facts: dict, answers: dict, user, *, expense, supplier=None) -> dict:
    vehicles = {
        row.pk: row
        for row in Vehicle.all_objects.filter(tenant=tenant, is_active=True)
    }
    vehicle = _selected_vehicle(facts, answers, vehicles)
    role = answers.get('vehicle_accounting_role') or (vehicle.accounting_role if vehicle else '')
    prompt_facts = document_facts({
        'number': facts['number'],
        'document_date': facts['document_date'].isoformat() if facts['document_date'] else '',
        'net_amount': str(facts['net_amount']),
        'tax_amount': str(facts['tax_amount']),
        'gross_amount': str(facts['gross_amount']),
        'currency': facts['currency'],
        'tax_category': facts['tax_category'],
        'lines': facts['lines'],
    })
    # Prompt se gradi ovdje da test može provjeriti da naziv dobavljača nije u njemu.
    facts['prompt'] = build_prompt(prompt_facts)
    ai = classify_event(prompt_facts, tenant)
    decision = _decide(facts, answers, ai, vehicle, role)
    profile = decision['posting_profile']
    status = decision['status']
    reason = decision['reason'] or ai.get('reason') or ''
    if profile == ExpensePostingProfile.COMPANY_PASSENGER_VEHICLE and not passenger_split_ready(
        facts['net_amount'],
        facts['tax_amount'],
    ):
        status = 'needs_information'
        reason = 'Omjer odbitka za ovo vozilo nije određen.'
    if profile == ExpensePostingProfile.FULL_VAT_VEHICLE and not _debit_configured(tenant, profile):
        status = 'needs_information'
        reason = 'Neto konto nije konfiguriran u pravilu knjiženja.'
    if profile in VEHICLE_PROFILES and vehicle is None:
        status = 'needs_information'
        reason = reason or 'Trošak vozila treba vezano vozilo.'
    inventory_block = _inventory_block(profile, vehicle, role, answers)
    if inventory_block:
        status = 'needs_information'
        reason = inventory_block
    questions = _questions(
        status=status,
        nature=ai.get('purchase_nature'),
        required=ai.get('required_context') or [],
        vehicle=vehicle,
        role=role,
        vehicles=list(vehicles.values()),
        profile=profile,
    )
    if questions and status != 'needs_information':
        if vehicle is None and _needs_vehicle(ai, profile):
            status = 'needs_information'
    fx, foreign_open = _foreign_gate(tenant, facts, answers)
    if foreign_open:
        questions.extend(_foreign_questions(facts, answers, fx))
        if status != 'needs_information':
            reason = 'Strani PDV treba tretman i tečaj.'
        status = 'needs_information'
    preview = None
    net_account = None
    remembered_rows = None
    if status != 'needs_information' and profile:
        preview, net_account, remembered_rows = _preview(
            tenant,
            user,
            expense=expense,
            supplier=supplier or (expense.supplier if expense is not None else None),
            facts=facts,
            profile=profile,
            vehicle=vehicle,
            role=role,
        )
        if preview is None and profile in VEHICLE_PROFILES:
            status = 'needs_information'
            reason = reason or 'Knjiženje se ne može prikazati.'
            preview = None
    if status == 'needs_information':
        preview = None
        line_accounts = []
    else:
        line_accounts = _resolved_line_accounts(
            profile,
            facts,
            net_account,
            remembered_rows,
            expense,
        )
    return {
        'status': status,
        'purchase_nature': ai.get('purchase_nature'),
        'posting_profile': profile,
        'profile_label': PROFILE_LABELS.get(profile, ''),
        'vehicle': _vehicle_payload(vehicle, role=role) if vehicle is not None else None,
        'decision_source': decision['source'],
        'reason': reason,
        'explanation': _explanation(profile, decision['source'], reason),
        'questions': questions if status == 'needs_information' else [],
        'preview': preview,
        'line_accounts': line_accounts,
        'document_date': facts['document_date'].isoformat() if facts['document_date'] else '',
        'foreign_vat': _foreign_payload(facts, answers, fx),
    }


def _decide(facts, answers, ai, vehicle, role) -> dict:
    nature = ai.get('purchase_nature') if ai.get('accepted') else None
    user_profile = answers.get('posting_profile') or ''
    if user_profile in PROFILES:
        return _choice(user_profile, 'user', 'resolved', 'Izbor korisnika za ovaj dokument.')
    if vehicle is not None and role in ROLE_TO_PROFILE and _role_applies(nature, ai, answers, vehicle):
        return _choice(
            ROLE_TO_PROFILE[role],
            'vehicle',
            'resolved',
            'Namjena vozila određuje tretman.',
        )
    if _missing_vehicle_context(nature, ai, vehicle, role):
        return _choice('', 'vehicle', 'needs_information', 'Nedostaje vozilo ili njegova namjena.')
    remembered = facts.get('remembered_profile') or ''
    if remembered in PROFILES:
        return _choice(remembered, 'remembered', 'needs_confirmation', 'Zapamćeni profil dobavljača i stavke.')
    history = (facts.get('history_profiles') or [None])[0]
    if history in PROFILES:
        return _choice(history, 'history', 'needs_confirmation', 'Profil iz povijesti dobavljača.')
    suggested = ai.get('suggested_profile')
    if suggested in PROFILES and ai.get('confidence') == 'high':
        return _choice(suggested, 'ai', 'needs_confirmation', ai.get('reason') or 'Prijedlog klasifikacije.')
    return _choice(ExpensePostingProfile.OPEX, 'rule', 'needs_confirmation', 'Operativni trošak.')


def _role_applies(nature, ai, answers, vehicle) -> bool:
    if nature == 'fuel' or 'vehicle' in (ai.get('required_context') or []):
        return True
    if answers.get('vehicle_id') or answers.get('vehicle_accounting_role'):
        return True
    return bool(vehicle and vehicle.accounting_role and nature == 'fuel')


def _missing_vehicle_context(nature, ai, vehicle, role) -> bool:
    fuelish = nature == 'fuel' or 'vehicle' in (ai.get('required_context') or [])
    if not fuelish:
        return False
    if vehicle is None or role not in ROLE_TO_PROFILE:
        return True
    return False


def _choice(profile, source, status, reason) -> dict:
    return {
        'posting_profile': profile or None,
        'source': source,
        'status': status,
        'reason': reason,
    }


def _inventory_block(profile, vehicle, role, answers) -> str:
    if vehicle is None or role != VehicleAccountingRole.INVENTORY_FOR_RESALE:
        return ''
    if profile == ExpensePostingProfile.ASSET_PURCHASE:
        return INVENTORY_ASSET_REASON
    if profile != ExpensePostingProfile.VEHICLE_INVENTORY_COST:
        return ''
    if vehicle.fixed_asset_id:
        return INVENTORY_FIXED_ASSET_REASON
    if vehicle.accounting_role != role and not answers.get('remember_vehicle_role'):
        return INVENTORY_ROLE_REASON
    return ''


def locked_account_detail(profile: str) -> str:
    if profile == ExpensePostingProfile.VEHICLE_INVENTORY_COST:
        return 'Ručni konto stavke nije dopušten za nabavnu vrijednost vozila na zalihi.'
    return 'Ručni konto stavke nije dopušten za službeno osobno vozilo.'


def assert_inventory_vehicle_profile(profile: str, vehicle) -> None:
    """Confirm/close: 422 prije knjiženja kad se zaliha vozila pokuša knjižiti kao imovina."""
    if vehicle is None or vehicle.accounting_role != VehicleAccountingRole.INVENTORY_FOR_RESALE:
        return
    if profile == ExpensePostingProfile.ASSET_PURCHASE:
        raise ValidationError({'posting_profile': INVENTORY_ASSET_REASON})


def _needs_vehicle(ai, profile) -> bool:
    return profile in VEHICLE_PROFILES or ai.get('purchase_nature') == 'fuel' or 'vehicle' in (
        ai.get('required_context') or []
    )


def _copy_foreign_facts(facts, *, net, vat, gross, country, vat_rate) -> None:
    facts['foreign_net_amount'] = net
    facts['foreign_vat_amount'] = vat
    facts['foreign_gross_amount'] = gross
    facts['foreign_vat_country'] = (country or '').strip().upper()
    facts['foreign_vat_rate'] = vat_rate


def _foreign_gate_active(facts) -> bool:
    vat = facts.get('foreign_vat_amount')
    country = facts.get('foreign_vat_country') or ''
    if vat is None:
        return False
    return Decimal(vat) > 0 and country not in ('', 'HR')


def _parse_rate(value):
    if value is None or value is False or value == '':
        return None
    text = str(value).strip().replace(' ', '').replace(',', '.')
    try:
        number = Decimal(text)
    except Exception:
        return None
    if number <= 0:
        return None
    return number


def _foreign_gate(tenant, facts, answers):
    facts['fx_snapshot'] = None
    facts['foreign_treatment'] = ''
    if not _foreign_gate_active(facts):
        return None, False
    treatment = answers.get('foreign_vat_treatment') or ''
    if treatment not in TREATMENTS:
        treatment = ''
    facts['foreign_treatment'] = treatment
    fx = resolve_fx_rate(
        tenant,
        facts.get('currency') or 'EUR',
        facts.get('document_date'),
        manual_rate=_parse_rate(answers.get('fx_rate')),
    )
    facts['fx_snapshot'] = fx
    return fx, not treatment or fx is None


_LOCAL_VAT_TITLE = {
    'DK': 'Danski PDV',
    'DE': 'Njemački PDV',
    'AT': 'Austrijski PDV',
    'SI': 'Slovenski PDV',
}


def _hr_amount(value) -> str:
    return f'{Decimal(value):.2f}'.replace('.', ',')


def _foreign_questions(facts, answers, fx) -> list[dict]:
    rows = []
    treatment = answers.get('foreign_vat_treatment') or ''
    if treatment not in TREATMENTS:
        country = facts.get('foreign_vat_country') or ''
        title = _LOCAL_VAT_TITLE.get(country, f'Strani PDV ({country})')
        currency = facts.get('currency') or ''
        rows.append({
            'key': 'foreign_vat_treatment',
            'type': 'single_choice',
            'label': (
                f'{title}: {_hr_amount(facts["foreign_vat_amount"])} {currency}. '
                'Kako želite tretirati strani PDV?'
            ),
            'required': True,
            'options': [
                {'value': 'expense', 'label': 'Uključi u trošak nabave'},
                {
                    'value': 'refund_receivable',
                    'label': 'Evidentiraj kao potraživanje za EU VAT Refund',
                },
            ],
        })
    if fx is None:
        currency = facts.get('currency') or ''
        rows.append({
            'key': 'fx_rate',
            'type': 'text',
            'label': f'Tečaj (koliko {currency} za 1 EUR)?',
            'required': True,
            'options': [],
        })
    return rows


def _foreign_payload(facts, answers, fx):
    if not _foreign_gate_active(facts):
        return None
    treatment = answers.get('foreign_vat_treatment') or ''
    if treatment not in TREATMENTS:
        treatment = ''
    net = facts.get('foreign_net_amount')
    gross = facts.get('foreign_gross_amount')
    return {
        'country': facts.get('foreign_vat_country') or '',
        'amount': f'{Decimal(facts["foreign_vat_amount"]):.2f}',
        'net_amount': '' if net is None else f'{Decimal(net):.2f}',
        'gross_amount': '' if gross is None else f'{Decimal(gross):.2f}',
        'currency': facts.get('currency') or '',
        'treatment': treatment,
        'fx_rate': '' if fx is None else format(fx.rate, 'f'),
        'fx_rate_source': '' if fx is None else fx.source,
        'fx_rate_date': '' if fx is None or fx.rate_date is None else fx.rate_date.isoformat(),
    }


def _stamp_foreign_snapshot(target, facts) -> None:
    if not _foreign_gate_active(facts):
        return
    fx = facts.get('fx_snapshot')
    treatment = facts.get('foreign_treatment') or ''
    if fx is None or treatment not in TREATMENTS:
        return
    gross = facts.get('foreign_gross_amount')
    vat = facts.get('foreign_vat_amount')
    net = facts.get('foreign_net_amount')
    if gross is None:
        gross = facts.get('gross_amount')
    if net is None and gross is not None and vat is not None:
        net = Decimal(gross) - Decimal(vat)
    target.foreign_net_amount = net
    target.foreign_vat_amount = vat
    target.foreign_gross_amount = gross
    target.foreign_vat_country = facts.get('foreign_vat_country') or ''
    target.foreign_vat_rate = facts.get('foreign_vat_rate')
    target.foreign_vat_treatment = treatment
    target.fx_rate = fx.rate
    target.fx_rate_source = fx.source
    target.fx_rate_date = fx.rate_date
    target.tax_amount = Decimal('0.00')
    target.refund_eligibility = 'candidate'
    if facts.get('currency'):
        target.currency = facts['currency']
    if gross is not None:
        target.amount = gross
    target.save()


def _bind_foreign_vat(expense, result, answers) -> None:
    foreign = result.get('foreign_vat') or None
    if not foreign:
        return
    expense.foreign_vat_country = foreign.get('country') or ''
    if foreign.get('amount'):
        expense.foreign_vat_amount = Decimal(foreign['amount'])
    if foreign.get('net_amount'):
        expense.foreign_net_amount = Decimal(foreign['net_amount'])
    if foreign.get('gross_amount'):
        expense.foreign_gross_amount = Decimal(foreign['gross_amount'])
        expense.amount = Decimal(foreign['gross_amount'])
    if foreign.get('currency'):
        expense.currency = foreign['currency']
    expense.tax_amount = Decimal('0.00')
    expense.refund_eligibility = 'candidate'
    treatment = answers.get('foreign_vat_treatment') or foreign.get('treatment') or ''
    if treatment in TREATMENTS:
        expense.foreign_vat_treatment = treatment
    if foreign.get('fx_rate'):
        expense.fx_rate = Decimal(foreign['fx_rate'])
        expense.fx_rate_source = foreign.get('fx_rate_source') or ''
        raw_date = foreign.get('fx_rate_date') or ''
        expense.fx_rate_date = date.fromisoformat(raw_date) if raw_date else None


def _questions(*, status, nature, required, vehicle, role, vehicles, profile) -> list[dict]:
    if status != 'needs_information' and not _needs_vehicle(
        {'purchase_nature': nature, 'required_context': required},
        profile,
    ):
        return []
    fuelish = nature == 'fuel' or 'vehicle' in required or profile in VEHICLE_PROFILES
    if not fuelish and profile != ExpensePostingProfile.FULL_VAT_VEHICLE:
        return []
    rows = []
    if vehicle is None and fuelish:
        rows.append({
            'key': 'vehicle_id',
            'type': 'vehicle_select',
            'label': 'Za koje vozilo je ovaj trošak?',
            'required': True,
            'options': [
                {'value': str(row.pk), 'label': _vehicle_label(row)}
                for row in vehicles
            ],
        })
    if vehicle is not None and role not in ROLE_TO_PROFILE and fuelish:
        rows.append({
            'key': 'vehicle_accounting_role',
            'type': 'single_choice',
            'label': 'Namjena vozila',
            'required': True,
            'options': [{'value': value, 'label': label} for value, label in ROLE_OPTIONS],
        })
        rows.append({
            'key': 'remember_vehicle_role',
            'type': 'boolean',
            'label': 'Zapamti namjenu za vozilo',
            'required': False,
            'options': [],
        })
    return rows


def _facts(
    *,
    tenant_id,
    supplier_id,
    number,
    document_date,
    net_amount,
    tax_amount,
    gross_amount,
    currency,
    tax_category,
    lines,
    vehicle_id,
    exclude_expense_id,
) -> dict:
    normalized = [normalize_line_text(item.get('description')) for item in lines]
    remembered = None
    if supplier_id and normalized:
        row = SupplierItemProfileMemory.all_objects.filter(
            tenant_id=tenant_id,
            supplier_id=supplier_id,
            line_signature=line_signature(normalized),
        ).first()
        if row is not None and row.posting_profile in PROFILES:
            remembered = row.posting_profile
    history = []
    if supplier_id:
        previous = Expense.all_objects.filter(
            tenant_id=tenant_id,
            supplier_id=supplier_id,
            posting_profile__in=VEHICLE_PROFILES,
        )
        if exclude_expense_id:
            previous = previous.exclude(pk=exclude_expense_id)
        latest = previous.order_by('-id').values_list('posting_profile', flat=True).first()
        if latest:
            history = [latest]
    return {
        'number': number or '',
        'document_date': document_date,
        'net_amount': Decimal(net_amount or 0),
        'tax_amount': Decimal(tax_amount or 0),
        'gross_amount': Decimal(gross_amount or 0),
        'currency': currency or 'EUR',
        'tax_category': tax_category or '',
        'lines': lines,
        'vehicle_id': vehicle_id,
        'remembered_profile': remembered,
        'history_profiles': history,
        'supplier_id': supplier_id,
    }


def _selected_vehicle(facts, answers, vehicles: dict):
    raw = answers.get('vehicle_id') or facts.get('vehicle_id')
    if not raw:
        return None
    try:
        vehicle_id = int(raw)
    except (TypeError, ValueError):
        return None
    return vehicles.get(vehicle_id)


def _answers(answers: dict | None) -> dict:
    data = dict(answers or {})
    profile = data.get('posting_profile') or ''
    if profile not in PROFILES:
        data['posting_profile'] = ''
    data['remember_vehicle_role'] = bool(data.get('remember_vehicle_role'))
    role = data.get('vehicle_accounting_role') or ''
    if role not in dict(ROLE_OPTIONS):
        data['vehicle_accounting_role'] = ''
    treatment = str(data.get('foreign_vat_treatment') or '')
    data['foreign_vat_treatment'] = treatment if treatment in TREATMENTS else ''
    return data


def _debit_configured(tenant, profile: str) -> bool:
    rules = PostingRule.all_objects.filter(
        tenant=tenant,
        document_type='expense_approved',
        is_active=True,
    )
    for rule in rules:
        condition = rule.condition or {}
        profiles = condition.get('posting_profile') or []
        if isinstance(profiles, str):
            profiles = [profiles]
        if profile in profiles and (rule.debit_account_code or '').strip():
            return True
    return False


def _resolved_line_accounts(profile, facts, net_account, remembered_rows, expense) -> list[dict]:
    count = len(facts['lines']) or 1
    if profile in (
        ExpensePostingProfile.GOODS_INWARD_COST,
        ExpensePostingProfile.FULL_VAT_VEHICLE,
    ):
        return _account_rows(count, net_account, 'posting_rule')
    if profile in ACCOUNT_LOCKED_PROFILES:
        return []
    if profile == ExpensePostingProfile.OPEX:
        remembered = _remembered_rows(remembered_rows)
        if remembered:
            return remembered
        return _account_rows(count, _category_account(expense), 'category')
    return []


def _account_rows(count: int, account, source: str) -> list[dict]:
    if account is None or count < 1:
        return []
    return [
        {
            'position': index,
            'account_id': account.pk,
            'account_code': account.account_code,
            'account_name': account.account_name,
            'source': source,
        }
        for index in range(1, count + 1)
    ]


def _remembered_rows(rows) -> list[dict]:
    if not rows:
        return []
    return [
        {
            'position': row['position'],
            'account_id': row['account_id'],
            'account_code': row['account_code'],
            'account_name': row['account_name'],
            'source': 'remembered',
        }
        for row in rows
    ]


def _category_account(expense):
    if expense is None or not getattr(expense, 'category_id', None):
        return None
    account = getattr(expense.category, 'default_account', None)
    if account is None or not account.is_active or not account.is_postable:
        return None
    return account


def _single_net_account(plan):
    if plan is None:
        return None
    accounts = [line.debit_account for line in plan.lines if line.amount_field == 'net_amount']
    if not accounts:
        return None
    first = accounts[0]
    if any(account.pk != first.pk for account in accounts[1:]):
        return None
    return first


def _preview(tenant, user, *, expense, supplier, facts, profile, vehicle, role=''):
    if supplier is None or user is None or not getattr(user, 'is_authenticated', True):
        return None, None, None
    stored_role = vehicle.accounting_role if vehicle is not None else ''
    try:
        with transaction.atomic():
            sid = transaction.savepoint()
            try:
                if vehicle is not None and role in ROLE_TO_PROFILE:
                    vehicle.accounting_role = role
                target = expense
                if target is None:
                    target = _temporary_expense(
                        tenant, user, supplier, facts, profile, vehicle,
                    )
                else:
                    target.posting_profile = profile
                    target.vehicle = vehicle
                    target.expense_account = None
                    target.save()
                _stamp_foreign_snapshot(target, facts)
                remembered = (
                    load_remembered_lines(target)
                    if profile == ExpensePostingProfile.OPEX
                    else None
                )
                plan = build_document_posting_plan(tenant, target, 'expense_approved')
                return _preview_rows(plan), _single_net_account(plan), remembered
            finally:
                transaction.savepoint_rollback(sid)
    except Exception:
        return None, None, None
    finally:
        if vehicle is not None:
            vehicle.accounting_role = stored_role
        if expense is not None and expense.pk:
            expense.refresh_from_db()


def _temporary_expense(tenant, user, supplier, facts, profile, vehicle) -> Expense:
    category = ExpenseCategory.all_objects.filter(tenant=tenant, is_active=True).first()
    if category is None:
        category = ExpenseCategory.all_objects.create(tenant=tenant, name='Pregled knjiženja')
    created = Expense.all_objects.create(
        tenant=tenant,
        expense_number=f'RV-{uuid4().hex[:18]}',
        status='draft',
        category=category,
        supplier=supplier,
        amount=facts['gross_amount'] if facts['gross_amount'] > 0 else Decimal('0.01'),
        tax_amount=facts['tax_amount'],
        currency=facts['currency'],
        expense_date=facts['document_date'] or date.today(),
        description=facts['lines'][0]['description'] if facts['lines'] else 'Pregled',
        created_by=user,
        posting_profile=profile,
        vehicle=vehicle,
        receipt_number=facts['number'][:50],
    )
    for index, line in enumerate(facts['lines'], start=1):
        gross = _line_amount(line.get('amount'))
        ExpenseLine.all_objects.create(
            tenant=tenant,
            expense=created,
            position=index,
            description=line.get('description') or 'Stavka',
            net_amount=Decimal('0.00'),
            vat_amount=Decimal('0.00'),
            gross_amount=gross,
        )
    return created


def _preview_rows(plan) -> list[dict] | None:
    if plan is None:
        return None
    rows = []
    for line in plan.lines:
        amount = str(line.amount)
        rows.append({
            'account_code': line.debit_account.account_code,
            'account_name': line.debit_account.account_name,
            'debit': amount,
            'credit': '0.00',
        })
        rows.append({
            'account_code': line.credit_account.account_code,
            'account_name': line.credit_account.account_name,
            'debit': '0.00',
            'credit': amount,
        })
    return rows


def _explanation(profile, source, reason) -> str:
    label = PROFILE_LABELS.get(profile, 'Knjiženje')
    if reason:
        return f'{label}. {reason}'
    return label


def _vehicle_payload(vehicle, role: str | None = None) -> dict:
    chosen = role if role is not None else vehicle.accounting_role
    return {
        'id': vehicle.pk,
        'name': vehicle.name,
        'vin': vehicle.vin,
        'registration_plate': vehicle.registration_plate,
        'accounting_role': chosen or '',
    }


def _vehicle_label(vehicle) -> str:
    plate = vehicle.registration_plate or vehicle.vin or ''
    if plate:
        return f'{vehicle.name} · {plate}'
    return vehicle.name


def _expense_line_texts(expense: Expense):
    from domains.finance.services.posting_memory import expense_line_facts

    return expense_line_facts(expense)


def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _line_amount(value) -> Decimal:
    try:
        amount = Decimal(str(value or '0'))
    except Exception:
        return Decimal('0.00')
    if amount < 0:
        return Decimal('0.00')
    return amount.quantize(Decimal('0.01'))
