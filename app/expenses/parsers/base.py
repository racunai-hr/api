from __future__ import annotations

from typing import Protocol

from expenses.parsers.types import ParseResult


class ExpenseImportParser(Protocol):
    source: str

    def parse(self, content: bytes | str, *, filename: str = '') -> ParseResult: ...


_REGISTRY: dict[str, type[ExpenseImportParser]] = {}


def register_parser(parser_cls: type[ExpenseImportParser]) -> type[ExpenseImportParser]:
    _REGISTRY[parser_cls.source] = parser_cls
    return parser_cls


def get_parser(source: str) -> ExpenseImportParser:
    parser_cls = _REGISTRY.get(source)
    if parser_cls is None:
        raise ValueError(f'Nepoznat parser izvor: {source}')
    return parser_cls()
