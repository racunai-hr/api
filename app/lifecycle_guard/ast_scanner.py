from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from lifecycle_guard.config import LifecycleProtectedModel

APP_ROOT = Path(__file__).resolve().parent.parent

SKIP_PATH_PARTS = frozenset({'migrations', 'tests', '__pycache__', 'lifecycle_guard'})

MANAGER_ATTRS = frozenset({'objects', 'all_objects'})


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    model: str
    field: str
    message: str
    snippet: str


def scan_for_violations(
    registry: tuple[LifecycleProtectedModel, ...],
    *,
    app_root: Path | None = None,
) -> list[Violation]:
    root = app_root or APP_ROOT
    violations: list[Violation] = []

    scan_roots: set[Path] = set()
    for entry in registry:
        for package in entry.scan_packages:
            scan_roots.add(root / package)

    for scan_root in sorted(scan_roots, key=lambda p: p.as_posix()):
        if not scan_root.is_dir():
            continue
        for path in sorted(scan_root.rglob('*.py'), key=lambda p: p.as_posix()):
            if SKIP_PATH_PARTS.intersection(path.parts):
                continue
            rel = path.relative_to(root).as_posix()
            file_violations = _scan_file(path, rel, registry)
            violations.extend(file_violations)

    return sorted(violations, key=lambda v: (v.path, v.line))


def scan_python_source(
    source: str,
    *,
    path: str,
    registry: tuple[LifecycleProtectedModel, ...],
) -> list[Violation]:
    """Scan a single source string (used by unit tests)."""
    violations: list[Violation] = []
    for entry in registry:
        if _is_allowed_module(path, entry):
            continue
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError:
            continue
        visitor = _LifecycleVisitor(path=path, config=entry, source_lines=source.splitlines())
        visitor.visit(tree)
        violations.extend(visitor.violations)
    return sorted(violations, key=lambda v: (v.path, v.line))


def _scan_file(
    path: Path,
    rel_path: str,
    registry: tuple[LifecycleProtectedModel, ...],
) -> list[Violation]:
    source = path.read_text(encoding='utf-8')
    return scan_python_source(source, path=rel_path, registry=registry)


def _is_allowed_module(rel_path: str, config: LifecycleProtectedModel) -> bool:
    normalized = Path(rel_path).as_posix()
    return any(Path(allowed).as_posix() == normalized for allowed in config.allowed_modules)


def _root_name(node: ast.expr | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _root_name(node.value)
    if isinstance(node, ast.Call):
        return _root_name(node.func)
    return None


def _expr_is_model(node: ast.expr | None, model: str) -> bool:
    return _root_name(node) == model


def _annotation_is_model(node: ast.expr | None, model: str) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Name):
        return node.id == model
    if isinstance(node, ast.Subscript):
        return _annotation_is_model(node.value, model)
    if isinstance(node, ast.Attribute):
        return node.attr == model
    return False


def _is_protected_queryset_chain(node: ast.expr | None, model: str) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Name):
        return node.id == model
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id == model:
            return node.attr in MANAGER_ATTRS
        return _is_protected_queryset_chain(node.value, model)
    if isinstance(node, ast.Call):
        return _is_protected_queryset_chain(node.func, model)
    return False


def _update_touches_field(call: ast.Call, field: str) -> bool:
    return any(kw.arg == field for kw in call.keywords)


class _LifecycleVisitor(ast.NodeVisitor):
    def __init__(
        self,
        *,
        path: str,
        config: LifecycleProtectedModel,
        source_lines: list[str],
    ) -> None:
        self.path = path
        self.config = config
        self.source_lines = source_lines
        self.var_models: dict[str, str] = {}
        self.violations: list[Violation] = []

    def _record_violation(self, node: ast.AST, message: str) -> None:
        line = getattr(node, 'lineno', 1)
        snippet = ''
        if 1 <= line <= len(self.source_lines):
            snippet = self.source_lines[line - 1].strip()
        self.violations.append(
            Violation(
                path=self.path,
                line=line,
                model=self.config.model,
                field=self.config.field,
                message=message,
                snippet=snippet,
            ),
        )

    def _bind_name(self, name: str, expr: ast.expr | None) -> None:
        if _expr_is_model(expr, self.config.model):
            self.var_models[name] = self.config.model

    def _bind_annotation(self, name: str, annotation: ast.expr | None) -> None:
        if _annotation_is_model(annotation, self.config.model):
            self.var_models[name] = self.config.model

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name):
                self._bind_name(target.id, node.value)
            elif isinstance(target, ast.Attribute):
                self._check_field_assignment(target, node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name):
            self._bind_annotation(node.target.id, node.annotation)
            if node.value is not None:
                self._bind_name(node.target.id, node.value)
        elif isinstance(node.target, ast.Attribute):
            self._check_field_assignment(node.target, node)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        if isinstance(node.target, ast.Name) and _expr_is_model(node.iter, self.config.model):
            self.var_models[node.target.id] = self.config.model
        elif isinstance(node.target, ast.Name) and _is_protected_queryset_chain(node.iter, self.config.model):
            self.var_models[node.target.id] = self.config.model
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function_like(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_like(node)

    def _visit_function_like(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        saved = self.var_models
        self.var_models = dict(saved)
        for arg in node.args.args:
            self._bind_annotation(arg.arg, arg.annotation)
        for arg in node.args.kwonlyargs:
            self._bind_annotation(arg.arg, arg.annotation)
        if node.args.vararg is not None:
            self._bind_annotation(node.args.vararg.arg, node.args.vararg.annotation)
        if node.args.kwarg is not None:
            self._bind_annotation(node.args.kwarg.arg, node.args.kwarg.annotation)
        self.generic_visit(node)
        self.var_models = saved

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and node.func.attr == 'update':
            receiver = node.func.value
            if (
                _is_protected_queryset_chain(receiver, self.config.model)
                and _update_touches_field(node, self.config.field)
            ):
                self._record_violation(
                    node,
                    f'direct {self.config.model}.{self.config.field} update via QuerySet.update()',
                )
        self.generic_visit(node)

    def _check_field_assignment(
        self,
        target: ast.Attribute,
        node: ast.Assign | ast.AnnAssign,
    ) -> None:
        if target.attr != self.config.field:
            return
        if not isinstance(target.value, ast.Name):
            return
        var_name = target.value.id
        if self.var_models.get(var_name) != self.config.model:
            return
        self._record_violation(
            node,
            f'direct assignment to {self.config.model}.{self.config.field}',
        )
