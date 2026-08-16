"""VAT box registry — single source of truth for the PDV module."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from django.db import models

FieldType = Literal['pair', 'scalar', 'bool']
Category = Literal['output', 'input', 'eu', 'adjustment', 'other']


class BoxValueSource(StrEnum):
    PAIR = 'pair'
    MARGIN_PAIR = 'margin_pair'
    CATEGORY_TOTAL_OUTPUT = 'cat_total_out'
    CATEGORY_TOTAL_INPUT = 'cat_total_in'
    AGGREGATE_BASE = 'aggregate_base'
    AGGREGATE_VAT = 'aggregate_vat'
    COMPUTED_VAT_DUE = 'computed_vat_due'
    BOOLEAN_NO_OUTPUT = 'boolean_no_output'
    ZERO = 'zero'


_PAIR_VALUE_SOURCES = frozenset(
    {
        BoxValueSource.PAIR,
        BoxValueSource.MARGIN_PAIR,
        BoxValueSource.CATEGORY_TOTAL_OUTPUT,
        BoxValueSource.CATEGORY_TOTAL_INPUT,
    }
)
_SCALAR_ONLY_VALUE_SOURCES = frozenset(
    {
        BoxValueSource.AGGREGATE_BASE,
        BoxValueSource.AGGREGATE_VAT,
        BoxValueSource.COMPUTED_VAT_DUE,
    }
)



@dataclass(frozen=True)
class VATBoxDefinition:
    code: str
    label: str
    pdv_field: str
    field_type: FieldType
    value_source: BoxValueSource
    category: Category
    active: bool
    implemented: bool
    reserved: bool
    mapping_rule: str = ''


def _box(
    code: str,
    label: str,
    *,
    field_type: FieldType,
    category: Category,
    value_source: BoxValueSource,
    active: bool = True,
    implemented: bool = False,
    reserved: bool = False,
    mapping_rule: str = '',
) -> VATBoxDefinition:
    return VATBoxDefinition(
        code=code,
        label=label,
        pdv_field=code,
        field_type=field_type,
        value_source=value_source,
        category=category,
        active=active,
        implemented=implemented,
        reserved=reserved,
        mapping_rule=mapping_rule,
    )


VAT_BOX_REGISTRY: tuple[VATBoxDefinition, ...] = (
    _box('000', 'Ukupni promet u razdoblju oporezivanja', field_type='scalar',
        value_source=BoxValueSource.ZERO, category='other'),
    _box('100', 'Isporuke u RH po stopi 0% (osim izvoza)', field_type='scalar',
        value_source=BoxValueSource.ZERO, category='other'),
    _box(
        '101',
        'Isporuke dobara unutar EU',
        field_type='scalar',
        value_source=BoxValueSource.ZERO,
        category='other',
        implemented=True,
        mapping_rule='Invoice EU outbound goods, 0% PDV, I-RA izlazni',
    ),
    _box('102', 'Isporuke dobara u treće zemlje (izvoz)', field_type='scalar',
        value_source=BoxValueSource.ZERO, category='other'),
    _box(
        '103',
        'Obavljene usluge unutar EU',
        field_type='scalar',
        value_source=BoxValueSource.ZERO,
        category='other',
        implemented=True,
        mapping_rule='Invoice EU outbound services (datum usluge), 0% PDV, I-RA izlazni',
    ),
    _box('104', 'Obavljene usluge u treće zemlje', field_type='scalar',
        value_source=BoxValueSource.ZERO, category='other'),
    _box('105', 'Isporuke NPS u RH', field_type='scalar',
        value_source=BoxValueSource.ZERO, category='other'),
    _box('106', 'Trosarinske naknade', field_type='scalar',
        value_source=BoxValueSource.ZERO, category='other'),
    _box('107', 'Posebni postupci oporezivanja', field_type='scalar',
        value_source=BoxValueSource.ZERO, category='other'),
    _box('108', 'Ostalo oslobođeno', field_type='scalar',
        value_source=BoxValueSource.ZERO, category='other'),
    _box('109', 'Ostalo neoporezivo', field_type='scalar',
        value_source=BoxValueSource.ZERO, category='other'),
    _box('110', 'Ostale isporuke', field_type='scalar',
        value_source=BoxValueSource.ZERO, category='other'),
    _box('111', 'Ukupno oslobođeno i neoporezivo', field_type='scalar',
        value_source=BoxValueSource.ZERO, category='other'),
    _box('200', 'Oporezive isporuke — ukupno', field_type='pair',
        value_source=BoxValueSource.CATEGORY_TOTAL_OUTPUT, category='output'),
    _box(
        '201',
        'Oporezive isporuke po stopi 5%',
        field_type='pair',
        value_source=BoxValueSource.PAIR,
        category='output',
        implemented=True,
        mapping_rule='Invoice item, stopa 5%, I-RA izlazni',
    ),
    _box(
        '202',
        'Oporezive isporuke po stopi 13%',
        field_type='pair',
        value_source=BoxValueSource.PAIR,
        category='output',
        implemented=True,
        mapping_rule='Invoice item, stopa 13%, I-RA izlazni',
    ),
    _box(
        '203',
        'Oporezive isporuke po stopi 25%',
        field_type='pair',
        value_source=BoxValueSource.PAIR,
        category='output',
        implemented=True,
        mapping_rule='Invoice item, stopa 25%, I-RA izlazni',
    ),
    _box(
        '204',
        'Prodaja dobara na daljinu unutar EU',
        field_type='pair',
        value_source=BoxValueSource.PAIR,
        category='output',
        implemented=True,
        mapping_rule='Invoice EU B2C, vat_procedure=eu_distance, I-RA izlazni',
    ),
    _box('205', 'Isporuke NPS unutar EU', field_type='pair',
        value_source=BoxValueSource.PAIR, category='output'),
    _box('206', 'Montaža i instaliranje u drugoj državi članici', field_type='pair',
        value_source=BoxValueSource.PAIR, category='output'),
    _box(
        '207',
        'Stjecanje dobara unutar EU po stopi 25% (II.7)',
        field_type='pair',
        value_source=BoxValueSource.PAIR,
        category='output',
        implemented=True,
        mapping_rule='JE D imovina u pripremi 0373 (osnovica), JE K 24022 (PDV obveza RC)',
    ),
    _box('208', 'Trostrani poslovi', field_type='pair',
        value_source=BoxValueSource.PAIR, category='output'),
    _box(
        '209',
        'Prijenos porezne obveze',
        field_type='pair',
        value_source=BoxValueSource.PAIR,
        category='output',
        implemented=True,
        mapping_rule='JE K 2401/24011 (građevinski prijenos, tuzemni RC) — II.9',
    ),
    _box(
        '210',
        'Primljene usluge unutar EU (B2B)',
        field_type='pair',
        value_source=BoxValueSource.PAIR,
        category='output',
        implemented=True,
        mapping_rule='JE K 24032 (EU B2B usluge RC) — II.10',
    ),
    _box('211', 'Prodaja rabljenih dobara, umjetničkih djela i kolekcionarskih predmeta', field_type='pair',
        value_source=BoxValueSource.PAIR, category='output'),
    _box('212', 'Putničke agencije (marža)', field_type='pair',
        value_source=BoxValueSource.PAIR, category='output'),
    _box('213', 'Stjecanje unutar EU s ugradnjom u drugoj državi', field_type='pair',
        value_source=BoxValueSource.PAIR, category='output'),
    _box(
        '214',
        'Isporuke putem elektroničkog sučelja',
        field_type='pair',
        value_source=BoxValueSource.PAIR,
        category='output',
        implemented=True,
        mapping_rule='Invoice marketplace/e-interface, vat_procedure=eu_electronic, I-RA izlazni',
    ),
    _box(
        '215',
        'Isporuke u okviru posebnog postupka OSS',
        field_type='pair',
        value_source=BoxValueSource.PAIR,
        category='output',
        implemented=True,
        mapping_rule='Invoice OSS e-trgovina EU, vat_procedure=oss, strana stopa PDV, I-RA izlazni',
    ),
    _box('300', 'Ukupno pretporez', field_type='pair',
        value_source=BoxValueSource.CATEGORY_TOTAL_INPUT, category='input'),
    _box('301', 'Pretporez od isporuka u RH po stopi 5%', field_type='pair',
        value_source=BoxValueSource.PAIR, category='input'),
    _box('302', 'Pretporez od isporuka u RH po stopi 13%', field_type='pair',
        value_source=BoxValueSource.PAIR, category='input'),
    _box(
        '303',
        'Pretporez od isporuka u RH po stopi 25%',
        field_type='pair',
        value_source=BoxValueSource.PAIR,
        category='input',
        implemented=True,
        mapping_rule='Expense s PDV 25% / JE D 1400, U-RA ulazni',
    ),
    _box('304', 'Pretporez — uvoz', field_type='pair',
        value_source=BoxValueSource.PAIR, category='input'),
    _box('305', 'Pretporez — stjecanje dobara unutar EU', field_type='pair',
        value_source=BoxValueSource.PAIR, category='input'),
    _box(
        '306',
        'Pretporez — primljene usluge unutar EU',
        field_type='pair',
        value_source=BoxValueSource.PAIR,
        category='input',
        implemented=True,
        mapping_rule='JE D 14032, pretporez na EU B2B usluge — III.10',
    ),
    _box(
        '307',
        'Pretporez od stjecanja dobara unutar EU po stopi 25% (III.7)',
        field_type='pair',
        value_source=BoxValueSource.PAIR,
        category='input',
        implemented=True,
        mapping_rule='JE D 14022, pretporez na EU stjecanje dobara',
    ),
    _box(
        '308',
        'Pretporez — obveza u drugoj državi članici',
        field_type='pair',
        value_source=BoxValueSource.PAIR,
        category='input',
        implemented=True,
        mapping_rule='Expense/JE IOSS, vat_procedure=ioss ili konto 14042, U-RA ulazni',
    ),
    _box(
        '309',
        'Pretporez — prijenos porezne obveze',
        field_type='pair',
        value_source=BoxValueSource.PAIR,
        category='input',
        implemented=True,
        mapping_rule='JE D 1401/14011, pretporez tuzemni RC (građevina, B2B usluge) — III.9',
    ),
    _box('310', 'Pretporez — manji prijetvor', field_type='pair',
        value_source=BoxValueSource.PAIR, category='input'),
    _box('311', 'Pretporez — posebni postupci', field_type='pair',
        value_source=BoxValueSource.PAIR, category='input'),
    _box('312', 'Pretporez — ostalo', field_type='pair',
        value_source=BoxValueSource.PAIR, category='input'),
    _box('313', 'Pretporez — ne priznat', field_type='pair',
        value_source=BoxValueSource.PAIR, category='input'),
    _box('314', 'Pretporez — ostalo ulazno', field_type='pair',
        value_source=BoxValueSource.PAIR, category='input'),
    _box('315', 'Nepriznati pretporez', field_type='scalar',
        value_source=BoxValueSource.ZERO, category='input'),
    _box('400', 'Obveza PDV-a (za uplatu) / povrat', field_type='scalar',
        value_source=BoxValueSource.COMPUTED_VAT_DUE, category='adjustment'),
    _box('500', 'Ispravak pretporeza iz prethodnog razdoblja', field_type='scalar',
        value_source=BoxValueSource.ZERO, category='adjustment'),
    _box(
        '610',
        'Ispravak pretporeza — ukupno (VIII.1)',
        field_type='scalar',
        value_source=BoxValueSource.AGGREGATE_BASE,
        category='adjustment',
        implemented=True,
        mapping_rule='VIII.1 zbroj 611+612+613+614+615 — agregat iz ledgera',
    ),
    _box(
        '611',
        'Ispravak pretporeza — nabava nekretnina (VIII.1.1)',
        field_type='scalar',
        value_source=BoxValueSource.AGGREGATE_VAT,
        category='adjustment',
        implemented=True,
        mapping_rule='JE D nekretnine (05x, 026) — nabava VIII.1.1',
    ),
    _box(
        '612',
        'Ispravak pretporeza — ostalo (VIII.1)',
        field_type='scalar',
        value_source=BoxValueSource.AGGREGATE_BASE,
        category='adjustment',
        implemented=True,
        mapping_rule='JE D osobni automobili (032x) — nabava VIII.1.2',
    ),
    _box(
        '613',
        'Ispravak pretporeza — PDV komponenta (VIII.1)',
        field_type='scalar',
        value_source=BoxValueSource.AGGREGATE_VAT,
        category='adjustment',
        implemented=True,
        mapping_rule='JE K osobni automobili (032x) — prodaja VIII.1.3',
    ),
    _box(
        '614',
        'Ispravak pretporeza — nabava ostale DI (VIII.1.4)',
        field_type='scalar',
        value_source=BoxValueSource.AGGREGATE_BASE,
        category='adjustment',
        implemented=True,
        mapping_rule='JE D ostala DI (030–031x) ili EU Expense 0% bez RC JE — VIII.1.4',
    ),
    _box(
        '615',
        'Ispravak pretporeza — prodaja ostale DI (VIII.1.5)',
        field_type='scalar',
        value_source=BoxValueSource.AGGREGATE_VAT,
        category='adjustment',
        implemented=True,
        mapping_rule='JE K ostala DI (030–031x, 05x) — prodaja VIII.1.5',
    ),
    _box('620', 'Ostale isporuke', field_type='scalar',
        value_source=BoxValueSource.ZERO, category='other', active=False, reserved=True),
    _box('630', 'Prijenos dobara u drugu državu članicu', field_type='scalar',
        value_source=BoxValueSource.ZERO, category='other'),
    _box('640', 'Primljene isporuke — prijenos obveze', field_type='scalar',
        value_source=BoxValueSource.ZERO, category='other'),
    _box('650', 'Obavljene isporuke — prijenos obveze', field_type='scalar',
        value_source=BoxValueSource.ZERO, category='other'),
    _box('660', 'Nema prometa u razdoblju', field_type='bool',
        value_source=BoxValueSource.BOOLEAN_NO_OUTPUT, category='other'),
    _box('701', 'Marža — rabljena dobra', field_type='pair',
        value_source=BoxValueSource.MARGIN_PAIR, category='other'),
    _box('702', 'Marža — umjetnička djela', field_type='pair',
        value_source=BoxValueSource.MARGIN_PAIR, category='other'),
    _box('703', 'Marža — kolekcionarski predmeti', field_type='pair',
        value_source=BoxValueSource.MARGIN_PAIR, category='other'),
    _box('704', 'Marža — antikviteti', field_type='pair',
        value_source=BoxValueSource.MARGIN_PAIR, category='other'),
)


def _validate_registry(registry: tuple[VATBoxDefinition, ...]) -> None:
    codes = [definition.code for definition in registry]
    if len(codes) != len(set(codes)):
        duplicates = sorted({code for code in codes if codes.count(code) > 1})
        raise ValueError(f'Duplicate VAT box codes: {duplicates}')
    if codes != sorted(codes, key=int):
        raise ValueError('VAT_BOX_REGISTRY must be sorted by int(code) ascending')
    for definition in registry:
        if definition.reserved and definition.implemented:
            raise ValueError(f'Box {definition.code}: reserved=True requires implemented=False')
        if definition.implemented and not definition.active:
            raise ValueError(f'Box {definition.code}: implemented=True requires active=True')
        if definition.implemented and not definition.mapping_rule:
            raise ValueError(f'Box {definition.code}: implemented=True requires mapping_rule')
        _validate_value_source(definition)


def _validate_value_source(definition: VATBoxDefinition) -> None:
    code = definition.code
    field_type = definition.field_type
    value_source = definition.value_source

    if value_source in _PAIR_VALUE_SOURCES and field_type != 'pair':
        raise ValueError(
            f'Box {code}: value_source {value_source} requires field_type=pair, got {field_type}'
        )
    if value_source in _SCALAR_ONLY_VALUE_SOURCES and field_type != 'scalar':
        raise ValueError(
            f'Box {code}: value_source {value_source} requires field_type=scalar, got {field_type}'
        )
    if value_source == BoxValueSource.BOOLEAN_NO_OUTPUT and field_type != 'bool':
        raise ValueError(
            f'Box {code}: value_source {value_source} requires field_type=bool, got {field_type}'
        )
    if value_source == BoxValueSource.ZERO and field_type not in ('scalar', 'bool'):
        raise ValueError(
            f'Box {code}: value_source zero requires field_type=scalar or bool, got {field_type}'
        )
    if value_source == BoxValueSource.COMPUTED_VAT_DUE and code != '400':
        raise ValueError(f'Box {code}: computed_vat_due is only valid for box 400')
    if value_source == BoxValueSource.BOOLEAN_NO_OUTPUT and code != '660':
        raise ValueError(f'Box {code}: boolean_no_output is only valid for box 660')
    if value_source == BoxValueSource.CATEGORY_TOTAL_OUTPUT and code != '200':
        raise ValueError(f'Box {code}: cat_total_out is only valid for box 200')
    if value_source == BoxValueSource.CATEGORY_TOTAL_INPUT and code != '300':
        raise ValueError(f'Box {code}: cat_total_in is only valid for box 300')
    if value_source == BoxValueSource.MARGIN_PAIR and code not in {'701', '702', '703', '704'}:
        raise ValueError(f'Box {code}: margin_pair is only valid for boxes 701–704')
    if value_source == BoxValueSource.AGGREGATE_BASE and code not in {'610', '612', '614'}:
        raise ValueError(f'Box {code}: aggregate_base is only valid for boxes 610, 612, 614')
    if value_source == BoxValueSource.AGGREGATE_VAT and code not in {'611', '613', '615'}:
        raise ValueError(f'Box {code}: aggregate_vat is only valid for boxes 611, 613, 615')


_validate_registry(VAT_BOX_REGISTRY)


VATBox = models.TextChoices(
    'VATBox',
    [(f'BOX_{definition.code}', (definition.code, definition.label)) for definition in VAT_BOX_REGISTRY],
)


def active_boxes() -> tuple[VATBoxDefinition, ...]:
    return tuple(definition for definition in VAT_BOX_REGISTRY if definition.active)


def implemented_boxes() -> tuple[VATBoxDefinition, ...]:
    return tuple(definition for definition in VAT_BOX_REGISTRY if definition.implemented)


def registry_doc_rows() -> list[dict[str, str]]:
    rows = []
    for definition in VAT_BOX_REGISTRY:
        rule = definition.mapping_rule if definition.implemented else ('—' if definition.reserved else '—')
        rows.append(
            {
                'code': definition.code,
                'label': definition.label,
                'category': definition.category,
                'pdv_field': definition.pdv_field,
                'field_type': definition.field_type,
                'active': 'da' if definition.active else 'ne',
                'implemented': 'da' if definition.implemented else 'ne',
                'reserved': 'da' if definition.reserved else 'ne',
                'rule': rule,
            }
        )
    return rows


def render_pdv_mapping_markdown() -> str:
    header = (
        '# PDV mapiranje — ERP izvor → VATBox → polje obrasca\n\n'
        'Jedini izvor istine za definicije polja je '
        '`accounting/services/tax_forms/pdv/boxes.py` (`VAT_BOX_REGISTRY`). '
        'Ovaj dokument se mora držati sinkroniziranim s registryjem.\n\n'
        '| Kod | Oznaka | Kategorija | PDV polje | Tip | Aktivan | Implementiran | Rezerviran | Pravilo |\n'
        '| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n'
    )
    lines = [header]
    for row in registry_doc_rows():
        lines.append(
            f"| {row['code']} | {row['label']} | {row['category']} | {row['pdv_field']} | "
            f"{row['field_type']} | {row['active']} | {row['implemented']} | {row['reserved']} | {row['rule']} |\n"
        )
    return ''.join(lines)


def parse_pdv_mapping_markdown(content: str) -> list[dict[str, str]]:
    rows = []
    for line in content.splitlines():
        if not line.startswith('|') or line.startswith('| ---') or line.startswith('| Kod'):
            continue
        cells = [cell.strip() for cell in line.strip('|').split('|')]
        if len(cells) != 9:
            continue
        rows.append(
            {
                'code': cells[0],
                'label': cells[1],
                'category': cells[2],
                'pdv_field': cells[3],
                'field_type': cells[4],
                'active': cells[5],
                'implemented': cells[6],
                'reserved': cells[7],
                'rule': cells[8],
            }
        )
    return rows


def _pdv_mapping_doc_candidates() -> list[Path]:
    rel = Path('docs') / 'accounting' / 'pdv-mapping.md'
    here = Path(__file__).resolve()
    candidates: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = str(path.resolve())
        if key in seen:
            return
        seen.add(key)
        candidates.append(path)

    try:
        from django.conf import settings

        base = Path(settings.BASE_DIR)
        add(base / rel)  # Docker: /app/docs/...
        add(base.parent.parent / rel)  # checkout: erp/docs/...
    except Exception:
        pass
    if len(here.parents) > 5:
        add(here.parents[5].parent / rel)
    return candidates


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def pdv_mapping_doc_path() -> Path:
    """Canonical path: erp/docs/accounting/pdv-mapping.md."""
    candidates = _dedupe_paths(_pdv_mapping_doc_candidates())
    for path in candidates:
        if path.is_file():
            return path
    tried = '\n'.join(f'  - {path}' for path in candidates)
    raise FileNotFoundError(f'Unable to locate pdv-mapping.md.\nTried:\n{tried}')