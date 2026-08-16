from import_lint.config import FORBIDDEN_DOMAIN_PAIRS
from import_lint.scanner import resolve_domain, resolve_import_domain, scan_python_source


def test_resolve_domain_accounting_posting():
    assert resolve_domain('accounting/services/posting.py') == 'finance'


def test_resolve_domain_tax_forms():
    assert resolve_domain('accounting/services/tax_forms/pdv/build.py') == 'tax'


def test_resolve_domain_shared_is_neutral():
    assert resolve_import_domain('shared.money') is None


def test_forbidden_import_detected():
    source = 'from invoices.models import Invoice\n'
    violations = scan_python_source(
        source,
        path='accounting/services/posting.py',
        source_domain='finance',
    )
    assert len(violations) == 1
    assert violations[0].source_domain == 'finance'
    assert violations[0].target_domain == 'sales'
    assert 'invoices.models' in violations[0].imported_module


def test_allowed_import_not_flagged():
    source = 'from accounting.services.tax_forms.pdv.build import build_pdv_payload\n'
    violations = scan_python_source(
        source,
        path='domains/tax/facade.py',
        source_domain='tax',
    )
    assert violations == []


def test_platform_to_finance_forbidden():
    assert ('platform', 'finance') in FORBIDDEN_DOMAIN_PAIRS
