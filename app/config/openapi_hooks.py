"""drf-spectacular hooks for the računAI public DRF contract."""

from __future__ import annotations

# Paths under /api/ that must not appear in the OpenAPI contract.
EXCLUDED_PATH_PREFIXES = (
    '/api/ready',
    '/api/fiscal',
    '/api/schema',
    '/api/auth/login',
    '/api/auth/logout',
)


def preprocess_exclude_non_contract(endpoints, **kwargs):
    """Drop health, fiscal AS4, schema UI, and browsable-auth routes."""
    filtered = []
    for path, path_regex, method, callback in endpoints:
        if any(path.startswith(prefix) or path == prefix.rstrip('/') for prefix in EXCLUDED_PATH_PREFIXES):
            continue
        # Empty DefaultRouter API root
        if path.rstrip('/') in ('/api', '') and method.upper() == 'GET':
            continue
        filtered.append((path, path_regex, method, callback))
    return filtered


def postprocess_security(result, generator, request, public):
    """Ensure public auth token routes have empty security; others keep bearerAuth."""
    paths = result.get('paths') or {}
    for path, operations in paths.items():
        if not isinstance(operations, dict):
            continue
        for method, operation in operations.items():
            if method.startswith('x-') or not isinstance(operation, dict):
                continue
            if path in ('/api/auth/token/', '/api/auth/token/refresh/') or path.rstrip('/') in (
                '/api/auth/token',
                '/api/auth/token/refresh',
            ):
                operation['security'] = []
    return result
