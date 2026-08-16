"""Domain import lint — enforces DOMAIN_DEPENDENCY_MAP rules.

CI runs in warning mode until legacy apps are migrated to ``domains/``.
"""

from import_lint.config import FORBIDDEN_DOMAIN_PAIRS, NEUTRAL_PACKAGES
from import_lint.scanner import ImportViolation, scan_for_import_violations, scan_python_source

__all__ = [
    'FORBIDDEN_DOMAIN_PAIRS',
    'NEUTRAL_PACKAGES',
    'ImportViolation',
    'scan_for_import_violations',
    'scan_python_source',
]
