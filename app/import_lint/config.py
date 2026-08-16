"""Domain import rules derived from DOMAIN_DEPENDENCY_MAP.md."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DomainRule:
    name: str
    prefixes: tuple[str, ...]


# Longest-prefix match wins when resolving a module to a domain.
DOMAIN_RULES: tuple[DomainRule, ...] = (
    DomainRule('platform', ('domains.platform', 'accounts')),
    DomainRule('core', ('domains.core', 'settings')),
    DomainRule('mdm', ('partners',)),
    DomainRule('finance', ('domains.finance', 'accounting.services.posting', 'payments')),
    DomainRule('tax', ('domains.tax', 'accounting.services.tax_forms', 'accounting.services.submission')),
    DomainRule('sales', ('domains.sales', 'invoices')),
    DomainRule('purchasing', ('domains.purchasing', 'expenses')),
    DomainRule('integration', ('domains.integration', 'integrations', 'fiscal_gateway', 'super_integration', 'ubl')),
    DomainRule('banking', ('domains.banking', 'banking')),
    DomainRule('reporting', ('domains.reporting', 'dashboard')),
    DomainRule('assets', ('domains.assets',)),
    DomainRule('workflow', ('domains.workflow',)),
    DomainRule('dms', ('domains.dms',)),
    DomainRule('crm', ('domains.crm',)),
    DomainRule('inventory', ('domains.inventory',)),
    DomainRule('hr', ('domains.hr',)),
    # accounting root — finance unless a more specific prefix matched above
    DomainRule('finance', ('accounting',)),
)

NEUTRAL_PACKAGES = frozenset({
    'shared',
    'events',
    'lifecycle_guard',
    'import_lint',
    'config',
    'django',
    'rest_framework',
})

# Forbidden (source_domain, target_domain) pairs from DOMAIN_DEPENDENCY_MAP.md.
FORBIDDEN_DOMAIN_PAIRS = frozenset({
    ('tax', 'sales'),
    ('finance', 'sales'),
    ('finance', 'purchasing'),
    ('platform', 'finance'),
    ('platform', 'tax'),
    ('platform', 'sales'),
    ('platform', 'purchasing'),
    ('platform', 'integration'),
    ('platform', 'banking'),
    ('platform', 'reporting'),
    ('platform', 'assets'),
    ('platform', 'workflow'),
    ('platform', 'mdm'),
    ('core', 'finance'),
    ('core', 'tax'),
    ('core', 'sales'),
    ('core', 'purchasing'),
    ('core', 'integration'),
    ('core', 'banking'),
    ('core', 'reporting'),
    ('core', 'assets'),
    ('core', 'workflow'),
})

SKIP_PATH_PARTS = frozenset({'migrations', 'tests', '__pycache__', 'import_lint'})
