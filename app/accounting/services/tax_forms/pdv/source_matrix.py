"""Load, validate, and render the PDV EU source matrix (YAML SSOT)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_SEMVER_RE = re.compile(r'^\d+\.\d+\.\d+$')
_EVENT_ID_RE = re.compile(r'^[A-Z][A-Z0-9_]*$')
_VALID_STATUSES = frozenset({'Draft', 'Reviewed', 'Locked'})
_MATRIX_RELATIVE = Path('docs') / 'accounting' / 'pdv-eu-source-matrix.yaml'


def _resolve_source_matrix_paths() -> tuple[Path, Path]:
    """Locate YAML/MD under the ERP project root (works in CI and host checkout)."""
    here = Path(__file__).resolve()
    candidates: list[Path] = []
    for parent in here.parents:
        candidates.append(parent / _MATRIX_RELATIVE)
    for parent in Path.cwd().resolve().parents:
        candidates.append(parent / _MATRIX_RELATIVE)
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_file():
            return candidate, candidate.with_suffix('.md')
    raise FileNotFoundError(
        'pdv-eu-source-matrix.yaml not found; expected under docs/accounting/ '
        'relative to the ERP project root.'
    )


SOURCE_MATRIX_YAML_PATH, SOURCE_MATRIX_MD_PATH = _resolve_source_matrix_paths()

GENERATED_MD_BANNER = (
    '<!-- generated from pdv-eu-source-matrix.yaml — do not edit manually; '
    'run: python -m accounting.services.tax_forms.pdv.source_matrix -->'
)


class SourceMatrixValidationError(ValueError):
    """Raised when the source matrix YAML fails structural validation."""


@dataclass(frozen=True)
class SourceMatrixMetadata:
    version: str
    status: str
    owner: str
    last_reviewed: str | None


@dataclass(frozen=True)
class SourceMatrixEvent:
    event_id: str
    name: str
    source: str
    boxes: tuple[str, ...]
    example: str = ''
    supplier_ref: str = ''
    accounts: dict[str, str] | None = None
    vat_rate: float | None = None
    notes: str = ''


@dataclass(frozen=True)
class SourceMatrixInvariant:
    id: str
    rule: str


@dataclass(frozen=True)
class SourceMatrixDedupRule:
    rule_id: str
    event_id: str
    action: str
    notes: str = ''


@dataclass(frozen=True)
class SourceMatrix:
    metadata: SourceMatrixMetadata
    events: tuple[SourceMatrixEvent, ...]
    invariants: tuple[SourceMatrixInvariant, ...]
    dedup: tuple[SourceMatrixDedupRule, ...]
    account_ranges: dict[str, str]
    references: tuple[str, ...]
    raw: dict[str, Any]


def load_source_matrix(path: Path | None = None) -> SourceMatrix:
    yaml_path = path or SOURCE_MATRIX_YAML_PATH
    with yaml_path.open(encoding='utf-8') as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise SourceMatrixValidationError('Root YAML element must be a mapping.')
    return _parse_matrix(raw)


def validate_source_matrix(matrix: SourceMatrix) -> None:
    meta = matrix.metadata
    if not _SEMVER_RE.match(meta.version):
        raise SourceMatrixValidationError(f'Invalid semver version: {meta.version!r}')

    if meta.status not in _VALID_STATUSES:
        raise SourceMatrixValidationError(
            f'Invalid status {meta.status!r}; expected one of {sorted(_VALID_STATUSES)}.'
        )

    if meta.status == 'Draft':
        if meta.last_reviewed is not None:
            raise SourceMatrixValidationError('Draft matrix must have last_reviewed: null.')
    elif meta.last_reviewed is None:
        raise SourceMatrixValidationError(
            f'Status {meta.status!r} requires last_reviewed (YYYY-MM-DD).'
        )

    if not meta.owner.strip():
        raise SourceMatrixValidationError('owner must be non-empty.')

    event_ids: list[str] = []
    for event in matrix.events:
        if not _EVENT_ID_RE.match(event.event_id):
            raise SourceMatrixValidationError(
                f'Invalid event_id {event.event_id!r}; expected UPPER_SNAKE_CASE.'
            )
        if event.event_id in event_ids:
            raise SourceMatrixValidationError(f'Duplicate event_id: {event.event_id!r}')
        event_ids.append(event.event_id)

        if not event.name.strip():
            raise SourceMatrixValidationError(f'Event {event.event_id!r} requires name.')
        if not event.source.strip():
            raise SourceMatrixValidationError(f'Event {event.event_id!r} requires source.')
        if not event.boxes:
            raise SourceMatrixValidationError(f'Event {event.event_id!r} requires boxes.')

        for box in event.boxes:
            if not box.isdigit():
                raise SourceMatrixValidationError(
                    f'Event {event.event_id!r} has invalid box code {box!r}.'
                )

    invariant_ids: set[str] = set()
    for invariant in matrix.invariants:
        if invariant.id in invariant_ids:
            raise SourceMatrixValidationError(f'Duplicate invariant id: {invariant.id!r}')
        invariant_ids.add(invariant.id)
        if not invariant.rule.strip():
            raise SourceMatrixValidationError(f'Invariant {invariant.id!r} requires rule.')

    dedup_ids: set[str] = set()
    known_event_ids = set(event_ids)
    for rule in matrix.dedup:
        if rule.rule_id in dedup_ids:
            raise SourceMatrixValidationError(f'Duplicate dedup rule_id: {rule.rule_id!r}')
        dedup_ids.add(rule.rule_id)
        if rule.event_id not in known_event_ids:
            raise SourceMatrixValidationError(
                f'Dedup rule {rule.rule_id!r} references unknown event_id {rule.event_id!r}.'
            )
        if not rule.action.strip():
            raise SourceMatrixValidationError(f'Dedup rule {rule.rule_id!r} requires action.')


def eu_box_codes_from_matrix(matrix: SourceMatrix) -> frozenset[str]:
    codes: set[str] = set()
    for event in matrix.events:
        codes.update(event.boxes)
    return frozenset(codes)


def render_source_matrix_markdown(matrix: SourceMatrix) -> str:
    meta = matrix.metadata
    lines = [
        GENERATED_MD_BANNER,
        '',
        '# PDV EU — source matrix',
        '',
        'Strojno čitljiv izvor: [`pdv-eu-source-matrix.yaml`](pdv-eu-source-matrix.yaml).',
        'Ovaj dokument se generira iz YAML-a; ne uređivati ručno.',
        '',
        '## Metapodaci',
        '',
        '| Polje | Vrijednost |',
        '| --- | --- |',
        f'| Verzija | `{meta.version}` |',
        f'| Status | **{meta.status}** |',
        f'| Vlasnik | {meta.owner} |',
        f'| Zadnji pregled | {meta.last_reviewed or "—"} |',
        '',
        '## Procesni ugovor (status)',
        '',
        '| Status | Značenje | Tko | Što je dopušteno |',
        '| --- | --- | --- | --- |',
        '| **Draft** | Radna verzija | Developer | Slobodne izmjene sadržaja, strukture, `event_id`, box mapiranja |',
        '| **Reviewed** | Pregledano | Developer + računovođa | Manje korekcije (tipfelere, konta, stopa, dedup); nema promjene poslovne logike bez reviewa |',
        '| **Locked** | Poslovna semantika zaključana | Računovođa potvrda | Svaka promjena zahtijeva semver bump, ažuriranje `last_reviewed`, novi review računovođe i regression testove prije mergea koda koji ovisi o promjeni |',
        '',
        '**Locked nije samo YAML flag** — kod (`implemented=True`, mapping, ledger) smije ovisiti o Locked matrixu.',
        'PR-B ne mergea se dok matrix nije **Locked**.',
        '',
        '### Pravila verzioniranja (`version`)',
        '',
        '| Bump | Primjer | Kada |',
        '| --- | --- | --- |',
        '| **major** | `1.0.0` → `2.0.0` | Promjena poslovne semantike postojećeg `event_id`, promjena mapiranja boxova za postojeći događaj, promjena invarijanata koji utječu na izračun |',
        '| **minor** | `1.0.0` → `1.1.0` | Dodavanje novog `event_id` ili novih boxova **bez** promjene postojećih događaja |',
        '| **patch** | `1.0.0` → `1.0.1` | Ispravci opisa, komentara, primjera, dokumentacije — **bez** promjene semantike, mapiranja ili invarijanata |',
        '',
        '## Događaji (`event_id`)',
        '',
        'Stabilni identitet poslovnog pravila — **ne koristiti box kodove kao identitet događaja**.',
        '',
    ]

    for event in matrix.events:
        lines.extend([
            f'### `{event.event_id}` — {event.name}',
            '',
            f'- **Izvor:** `{event.source}`',
            f'- **Boxovi:** {", ".join(f"`{box}`" for box in event.boxes)}',
        ])
        if event.example:
            lines.append(f'- **Primjer:** {event.example}')
        if event.supplier_ref:
            lines.append(f'- **Dobavljač (ref):** `{event.supplier_ref}`')
        if event.vat_rate is not None:
            lines.append(f'- **Stopa PDV:** {event.vat_rate:.0%}')
        if event.accounts:
            lines.append('- **Konta:**')
            for key, code in sorted(event.accounts.items()):
                lines.append(f'  - `{key}`: `{code}`')
        if event.notes.strip():
            lines.append(f'- **Napomena:** {event.notes.strip()}')
        lines.append('')

    if matrix.account_ranges:
        lines.extend([
            '## Rasponi konta (EU)',
            '',
            '| Ključ | Raspon |',
            '| --- | --- |',
        ])
        for key, value in sorted(matrix.account_ranges.items()):
            lines.append(f'| `{key}` | `{value}` |')
        lines.append('')

    lines.extend([
        '## Invarijanti',
        '',
        '| ID | Pravilo |',
        '| --- | --- |',
    ])
    for invariant in matrix.invariants:
        lines.append(f'| `{invariant.id}` | `{invariant.rule}` |')
    lines.append('')

    lines.extend([
        '## Dedup pravila',
        '',
        '| rule_id | event_id | action | Napomena |',
        '| --- | --- | --- | --- |',
    ])
    for rule in matrix.dedup:
        note = rule.notes.strip().replace('\n', ' ') if rule.notes else '—'
        lines.append(f'| `{rule.rule_id}` | `{rule.event_id}` | `{rule.action}` | {note} |')
    lines.append('')

    if matrix.references:
        lines.extend([
            '## Reference',
            '',
        ])
        for ref in matrix.references:
            lines.append(f'- `{ref}`')
        lines.append('')

    return '\n'.join(lines).rstrip() + '\n'


def write_source_matrix_markdown(
    matrix: SourceMatrix | None = None,
    *,
    path: Path | None = None,
) -> Path:
    matrix = matrix or load_source_matrix()
    validate_source_matrix(matrix)
    md_path = path or SOURCE_MATRIX_MD_PATH
    md_path.write_text(render_source_matrix_markdown(matrix), encoding='utf-8')
    return md_path


def _parse_matrix(raw: dict[str, Any]) -> SourceMatrix:
    metadata = SourceMatrixMetadata(
        version=str(raw.get('version', '')).strip(),
        status=str(raw.get('status', '')).strip(),
        owner=str(raw.get('owner', '')).strip(),
        last_reviewed=raw.get('last_reviewed'),
    )

    events: list[SourceMatrixEvent] = []
    for item in raw.get('events') or []:
        if not isinstance(item, dict):
            raise SourceMatrixValidationError('Each event must be a mapping.')
        boxes_raw = item.get('boxes') or []
        events.append(
            SourceMatrixEvent(
                event_id=str(item['event_id']).strip(),
                name=str(item.get('name', '')).strip(),
                source=str(item.get('source', '')).strip(),
                boxes=tuple(str(box) for box in boxes_raw),
                example=str(item.get('example', '')).strip(),
                supplier_ref=str(item.get('supplier_ref', '')).strip(),
                accounts=dict(item['accounts']) if item.get('accounts') else None,
                vat_rate=item.get('vat_rate'),
                notes=str(item.get('notes', '')).strip(),
            )
        )

    invariants = tuple(
        SourceMatrixInvariant(id=str(item['id']).strip(), rule=str(item['rule']).strip())
        for item in (raw.get('invariants') or [])
    )

    dedup = tuple(
        SourceMatrixDedupRule(
            rule_id=str(item['rule_id']).strip(),
            event_id=str(item['event_id']).strip(),
            action=str(item['action']).strip(),
            notes=str(item.get('notes', '')).strip(),
        )
        for item in (raw.get('dedup') or [])
    )

    account_ranges = {
        str(key): str(value)
        for key, value in (raw.get('account_ranges') or {}).items()
    }

    references = tuple(str(ref) for ref in (raw.get('references') or []))

    return SourceMatrix(
        metadata=metadata,
        events=tuple(events),
        invariants=invariants,
        dedup=dedup,
        account_ranges=account_ranges,
        references=references,
        raw=raw,
    )


if __name__ == '__main__':
    written = write_source_matrix_markdown()
    print(f'Wrote {written}')
