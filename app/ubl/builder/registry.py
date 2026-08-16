from __future__ import annotations

from typing import Callable

from lxml import etree

from ubl.domain.document import UblDocument

RendererFn = Callable[[UblDocument], etree._Element]

_RENDERERS: dict[str, RendererFn] = {}


def register_renderer(version: str, fn: RendererFn) -> None:
    _RENDERERS[version] = fn


def get_renderer(version: str = '2025') -> RendererFn:
    try:
        return _RENDERERS[version]
    except KeyError as exc:
        supported = ', '.join(sorted(_RENDERERS)) or '(none)'
        raise ValueError(f'Nepoznata UBL verzija: {version!r}. Podržano: {supported}') from exc


def supported_versions() -> list[str]:
    return sorted(_RENDERERS)


def _register_defaults() -> None:
    from ubl.builder.hrcius2025 import build_hrcius_invoice_root

    register_renderer('2025', build_hrcius_invoice_root)


_register_defaults()
