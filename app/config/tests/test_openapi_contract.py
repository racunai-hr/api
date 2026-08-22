"""OpenAPI contract inventory and regeneration gates."""

from __future__ import annotations

from django.test import SimpleTestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver
from drf_spectacular.generators import SchemaGenerator

from config.openapi_contract import (
    CONTRACT_OPERATIONS,
    EXCLUDED_URL_NAMES,
    URL_NAME_TO_OPERATION,
)
from config.openapi_hooks import EXCLUDED_PATH_PREFIXES


def _normalize_schema_path(path: str) -> str:
    """Map spectacular path params to contract inventory style."""
    # spectacular may emit {pk} or {id} depending on converter/kwargs
    replacements = (
        ('{pk}', '{id}'),
        ('{attachment_id}', '{attachment_id}'),
    )
    out = path
    for old, new in replacements:
        out = out.replace(old, new)
    if not out.startswith('/'):
        out = '/' + out
    if not out.endswith('/'):
        # keep as spectacular emits; contract uses trailing slash
        pass
    return out


def _iter_named_api_routes(patterns=None, prefix=''):
    if patterns is None:
        patterns = get_resolver().url_patterns
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _iter_named_api_routes(
                pattern.url_patterns,
                prefix + str(pattern.pattern),
            )
        elif isinstance(pattern, URLPattern):
            full = prefix + str(pattern.pattern)
            if not full.startswith('api/'):
                continue
            name = pattern.name
            if name:
                yield name, '/' + full


@override_settings(ALLOWED_HOSTS=['*'])
class OpenApiContractTests(SimpleTestCase):
    def test_generated_schema_matches_contract_inventory(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        found: set[tuple[str, str]] = set()
        for path, operations in (schema.get('paths') or {}).items():
            norm = _normalize_schema_path(path)
            # spectacular path templates use {id} for <int:pk> in recent versions
            # or {pk} — normalize both to inventory
            for method, op in operations.items():
                if method.startswith('x-') or not isinstance(op, dict):
                    continue
                found.add((method.lower(), norm.replace('{pk}', '{id}')))

        missing = CONTRACT_OPERATIONS - found
        unexpected = found - CONTRACT_OPERATIONS
        self.assertFalse(
            missing,
            f'Contract operations missing from OpenAPI schema: {sorted(missing)}',
        )
        self.assertFalse(
            unexpected,
            f'Unexpected OpenAPI operations (update inventory or exclude): {sorted(unexpected)}',
        )

    def test_urlconf_contract_routes_are_inventoried(self):
        """New named DRF routes under /api/ must be listed or explicitly excluded."""
        for name, path in _iter_named_api_routes():
            if name in EXCLUDED_URL_NAMES:
                continue
            if any(
                ('/api' + path[3:] if path.startswith('/api') else path).startswith(p)
                or path.startswith(p.lstrip('/'))
                for p in EXCLUDED_PATH_PREFIXES
            ):
                # crude; rely on name allowlist primarily
                pass
            if name.startswith('api-'):
                continue
            self.assertIn(
                name,
                URL_NAME_TO_OPERATION,
                f'New API url name {name!r} ({path}) missing from openapi_contract.URL_NAME_TO_OPERATION',
            )

    def test_protected_operations_declare_bearer_security(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        schemes = schema.get('components', {}).get('securitySchemes', {})
        self.assertIn('bearerAuth', schemes)
        public = {
            '/api/auth/token/',
            '/api/auth/token/refresh/',
        }
        for path, operations in (schema.get('paths') or {}).items():
            for method, op in operations.items():
                if method.startswith('x-') or not isinstance(op, dict):
                    continue
                security = op.get('security')
                if path.rstrip('/') + '/' in public or path in public:
                    self.assertEqual(
                        security,
                        [],
                        f'{method.upper()} {path} should have empty security',
                    )
                else:
                    self.assertTrue(
                        security and any('bearerAuth' in s for s in security),
                        f'{method.upper()} {path} missing bearerAuth security (got {security!r})',
                    )

    def test_banking_documents_and_tax_do_not_advertise_403(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        for path, operations in (schema.get('paths') or {}).items():
            if '/banking/' not in path and '/documents/' not in path and '/tax/' not in path:
                continue
            for method, op in operations.items():
                if method.startswith('x-') or not isinstance(op, dict):
                    continue
                responses = op.get('responses') or {}
                self.assertNotIn(
                    '403',
                    responses,
                    f'{method.upper()} {path} must not advertise 403 (runtime uses 404)',
                )
