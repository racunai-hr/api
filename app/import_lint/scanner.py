"""AST scanner for cross-domain import violations."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from import_lint.config import (
    DOMAIN_RULES,
    FORBIDDEN_DOMAIN_PAIRS,
    NEUTRAL_PACKAGES,
    SKIP_PATH_PARTS,
    DomainRule,
)

APP_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class ImportViolation:
    path: str
    line: int
    source_domain: str
    target_domain: str
    imported_module: str
    message: str
    snippet: str


def scan_for_import_violations(*, app_root: Path | None = None) -> list[ImportViolation]:
    root = app_root or APP_ROOT
    violations: list[ImportViolation] = []

    for path in sorted(root.rglob('*.py'), key=lambda p: p.as_posix()):
        if SKIP_PATH_PARTS.intersection(path.parts):
            continue
        rel = path.relative_to(root).as_posix()
        source_domain = resolve_domain(rel)
        if source_domain is None:
            continue
        source = path.read_text(encoding='utf-8')
        violations.extend(
            scan_python_source(source, path=rel, source_domain=source_domain),
        )

    return sorted(violations, key=lambda v: (v.path, v.line))


def scan_python_source(
    source: str,
    *,
    path: str,
    source_domain: str,
) -> list[ImportViolation]:
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        return []

    source_lines = source.splitlines()
    violations: list[ImportViolation] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                violations.extend(
                    _check_import(
                        source_domain=source_domain,
                        imported_module=alias.name,
                        path=path,
                        line=node.lineno,
                        source_lines=source_lines,
                    ),
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            violations.extend(
                _check_import(
                    source_domain=source_domain,
                    imported_module=node.module,
                    path=path,
                    line=node.lineno,
                    source_lines=source_lines,
                ),
            )

    return violations


def resolve_domain(module_path: str) -> str | None:
    normalized = module_path.replace('/', '.').removesuffix('.py')
    if normalized.endswith('.__init__'):
        normalized = normalized[: -len('.__init__')]

    best: DomainRule | None = None
    best_len = -1
    for rule in DOMAIN_RULES:
        for prefix in rule.prefixes:
            if normalized == prefix or normalized.startswith(prefix + '.'):
                if len(prefix) > best_len:
                    best = rule
                    best_len = len(prefix)
    return best.name if best else None


def resolve_import_domain(imported_module: str) -> str | None:
    top_level = imported_module.split('.', 1)[0]
    if top_level in NEUTRAL_PACKAGES:
        return None
    return resolve_domain(imported_module)


def _check_import(
    *,
    source_domain: str,
    imported_module: str,
    path: str,
    line: int,
    source_lines: list[str],
) -> list[ImportViolation]:
    target_domain = resolve_import_domain(imported_module)
    if target_domain is None or target_domain == source_domain:
        return []

    if (source_domain, target_domain) not in FORBIDDEN_DOMAIN_PAIRS:
        return []

    snippet = ''
    if 1 <= line <= len(source_lines):
        snippet = source_lines[line - 1].strip()

    return [
        ImportViolation(
            path=path,
            line=line,
            source_domain=source_domain,
            target_domain=target_domain,
            imported_module=imported_module,
            message=(
                f'forbidden cross-domain import: {source_domain} → {target_domain} '
                f'({imported_module})'
            ),
            snippet=snippet,
        ),
    ]
