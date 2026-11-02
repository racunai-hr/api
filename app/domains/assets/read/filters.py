"""Query filters and pagination for the Assets read API."""

from __future__ import annotations

from dataclasses import dataclass

from accounting.models import FixedAssetOrigin, FixedAssetStatus

ASSET_STATUSES = frozenset(FixedAssetStatus.values)
ASSET_ORIGINS = frozenset(FixedAssetOrigin.values)


def _int(raw: str | None) -> int | None:
    if raw in (None, ''):
        return None
    return int(raw)


def parse_page(query) -> tuple[int, int]:
    page = max(1, _int(query.get('page')) or 1)
    page_size = _int(query.get('page_size')) or 20
    page_size = min(max(1, page_size), 100)
    return page, page_size


@dataclass(frozen=True)
class FixedAssetListFilters:
    status: str | None = None
    origin: str | None = None
    search: str = ''
    page: int = 1
    page_size: int = 20


def parse_fixed_asset_filters(query) -> FixedAssetListFilters:
    status = (query.get('status') or '').strip() or None
    if status and status not in ASSET_STATUSES:
        raise ValueError('status mora biti in_preparation, active ili disposed')
    origin = (query.get('origin') or '').strip() or None
    if origin and origin not in ASSET_ORIGINS:
        raise ValueError('origin mora biti purchase ili opening_balance')
    page, page_size = parse_page(query)
    return FixedAssetListFilters(
        status=status,
        origin=origin,
        search=(query.get('search') or '').strip(),
        page=page,
        page_size=page_size,
    )
