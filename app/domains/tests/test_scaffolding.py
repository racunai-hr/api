"""Smoke tests for domain facade scaffolding."""

import importlib

import pytest

DOMAIN_MODULES = [
    'domains.platform',
    'domains.core',
    'domains.finance',
    'domains.tax',
    'domains.sales',
    'domains.purchasing',
    'domains.integration',
    'domains.banking',
    'domains.reporting',
    'domains.assets',
    'domains.workflow',
    'domains.dms',
    'domains.crm',
    'domains.inventory',
    'domains.hr',
]

SHARED_MODULES = [
    'shared.money',
    'shared.vat',
    'shared.oib',
    'shared.iban',
    'shared.countries',
    'shared.dates',
    'shared.numbering',
    'shared.validators',
]


@pytest.mark.parametrize('module_name', DOMAIN_MODULES)
def test_domain_facade_imports(module_name: str):
    module = importlib.import_module(module_name)
    assert hasattr(module, 'MATURITY')


def test_finance_facade_exports_subledger_api():
    from domains import finance

    for name in ('create_subledger_item', 'allocate_payment', 'get_partner_aging'):
        assert hasattr(finance, name), f'domains.finance missing {name}'


@pytest.mark.parametrize('module_name', SHARED_MODULES)
def test_shared_module_imports(module_name: str):
    importlib.import_module(module_name)
