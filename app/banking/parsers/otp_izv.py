"""OTP Obrazac IZV (PDF) → parsed statements.

The text layer spaces glyphs inside numbers (gaps up to ~2.3pt) and mixes
columns. Words are rebuilt from character boxes; Duguje/Potražuje columns
come from the header, not from fixed page coordinates.
"""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from banking.parsers.types import ParsedBankStatement, ParsedBankTransaction

_DIGIT_JOIN_GAP = 2.45
_LETTER_JOIN_GAP = 1.35
_LINE_Y_TOL = 2.8
_LEFT_CURRENCY_X = 180

_DATE_RE = re.compile(r'^(\d{2})\.(\d{2})\.(\d{4})$')
_RB_RE = re.compile(r'^\d+\.$')
_AMOUNT_RE = re.compile(r'^(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}$')
_HR_IBAN_RE = re.compile(r'^HR\d{19}$')
_IBAN_RE = re.compile(r'^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$')
_DIGITS_RE = re.compile(r'^\d{6,}$')
_RATE_RE = re.compile(r'^\d+,\d{3,}$')


@dataclass(frozen=True)
class OtpWord:
    x: float
    y: float
    text: str


@dataclass(frozen=True)
class OtpIzvParseResult:
    statements: list[ParsedBankStatement]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class _Columns:
    opis_x: float
    duguje_x: float
    potrazuje_x: float

    @property
    def boundary(self) -> float:
        return (self.duguje_x + self.potrazuje_x) / 2


def parse_otp_izv(content: bytes | str) -> OtpIzvParseResult:
    raw = content if isinstance(content, bytes) else content.encode('utf-8')
    return parse_otp_izv_words(_extract_pages(raw))


def parse_otp_izv_words(pages: list[list[OtpWord]]) -> OtpIzvParseResult:
    statements: list[ParsedBankStatement] = []
    warnings: list[str] = []
    for page in pages:
        page_statements, page_warnings = _parse_page(page)
        statements.extend(page_statements)
        warnings.extend(page_warnings)
    if not statements:
        raise ValueError('OTP IZV: nema bloka s IBAN-om.')
    return OtpIzvParseResult(statements=statements, warnings=tuple(warnings))


def pdf_text(content: bytes) -> str:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(io.BytesIO(content))
    try:
        parts: list[str] = []
        for index, page in enumerate(pdf):
            if index >= 3:
                break
            textpage = page.get_textpage()
            try:
                parts.append(textpage.get_text_bounded() or '')
            finally:
                textpage.close()
        return '\n'.join(parts)
    finally:
        pdf.close()


def _extract_pages(content: bytes) -> list[list[OtpWord]]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(io.BytesIO(content))
    pages: list[list[OtpWord]] = []
    try:
        for page in pdf:
            textpage = page.get_textpage()
            try:
                pages.append(_words_from_textpage(textpage))
            finally:
                textpage.close()
    finally:
        pdf.close()
    return pages


def _words_from_textpage(textpage) -> list[OtpWord]:
    chars: list[tuple[float, float, float, str]] = []
    for index in range(textpage.count_chars()):
        ch = textpage.get_text_range(index, 1)
        if ch in '\r\n' or (not ch.strip() and ch != ' '):
            continue
        x0, y0, x1, _y1 = textpage.get_charbox(index)
        chars.append((x0, y0, x1, ch))
    if not chars:
        return []

    chars.sort(key=lambda item: (-item[1], item[0]))
    lines: list[list[tuple[float, float, float, str]]] = []
    anchors: list[float] = []
    for item in chars:
        if lines and abs(item[1] - anchors[-1]) <= _LINE_Y_TOL:
            lines[-1].append(item)
        else:
            lines.append([item])
            anchors.append(item[1])

    words: list[OtpWord] = []
    for line in lines:
        words.extend(_tokens(line))
    return words


def _tokens(chars: list[tuple[float, float, float, str]]) -> list[OtpWord]:
    ordered = sorted(chars, key=lambda item: item[0])
    words: list[OtpWord] = []
    buf = ''
    origin_x = 0.0
    origin_y = 0.0
    prev_x1: float | None = None

    def flush() -> None:
        nonlocal buf
        if buf:
            words.append(OtpWord(origin_x, origin_y, buf))
            buf = ''

    for x0, y0, x1, ch in ordered:
        if ch == ' ' or ch == '\u00a0':
            flush()
            prev_x1 = x1
            continue
        gap = None if prev_x1 is None else x0 - prev_x1
        if buf and gap is not None and not _can_join(buf[-1], ch, gap):
            flush()
        if not buf:
            origin_x = x0
            origin_y = y0
        buf += ch
        prev_x1 = x1
    flush()
    return words


def _parse_page(words: list[OtpWord]) -> tuple[list[ParsedBankStatement], list[str]]:
    lines = _group_lines(words)
    if not lines:
        return [], []

    columns = _columns(lines)
    seq = _statement_seq(lines)
    statement_day = _statement_day(lines)
    number = f'{statement_day.year}-{seq}'

    header_indexes = [index for index, line in enumerate(lines) if _is_account_header(line)]
    statements: list[ParsedBankStatement] = []
    warnings: list[str] = []
    for position, start in enumerate(header_indexes):
        end = header_indexes[position + 1] if position + 1 < len(header_indexes) else len(lines)
        section = lines[start:end]
        parsed, warning = _parse_section(section, columns, number, statement_day)
        if warning:
            warnings.append(warning)
        if parsed is not None:
            statements.append(parsed)
    return statements, warnings


def _group_lines(words: list[OtpWord]) -> list[list[OtpWord]]:
    ordered = sorted(words, key=lambda word: (-word.y, word.x))
    lines: list[list[OtpWord]] = []
    anchors: list[float] = []
    for word in ordered:
        if lines and abs(word.y - anchors[-1]) <= _LINE_Y_TOL:
            lines[-1].append(word)
        else:
            lines.append([word])
            anchors.append(word.y)
    for line in lines:
        line.sort(key=lambda word: word.x)
    return lines


def _can_join(prev: str, nxt: str, gap: float) -> bool:
    """Digits inside an amount may sit ~2.3pt apart. A letter must not swallow the next digit."""
    if gap <= 0:
        return True
    if prev.isdigit() and (nxt.isdigit() or nxt in ',.'):
        return gap <= _DIGIT_JOIN_GAP
    if prev in ',.' and (nxt.isdigit() or nxt in ',.'):
        return gap <= _DIGIT_JOIN_GAP
    return gap <= _LETTER_JOIN_GAP


def _fold(text: str) -> str:
    return (
        text.lower()
        .replace('č', 'c')
        .replace('ć', 'c')
        .replace('ž', 'z')
        .replace('š', 's')
        .replace('đ', 'd')
    )


def _line_text(line: list[OtpWord]) -> str:
    return ' '.join(word.text for word in line)


def _columns(lines: list[list[OtpWord]]) -> _Columns:
    duguje_x = None
    potrazuje_x = None
    opis_x = None
    for line in lines:
        for word in line:
            folded = _fold(word.text).rstrip(':')
            if folded == 'duguje':
                duguje_x = word.x
            elif folded == 'potrazuje':
                potrazuje_x = word.x
            elif folded in ('opis', 'placanja') or folded.startswith('opis'):
                candidate = word.x - (25 if folded == 'placanja' else 0)
                if opis_x is None or candidate < opis_x:
                    opis_x = candidate
    if duguje_x is None or potrazuje_x is None or potrazuje_x <= duguje_x:
        raise ValueError('OTP IZV: nema kolona Duguje i Potražuje.')
    if opis_x is None:
        opis_x = duguje_x - 200
    return _Columns(opis_x=opis_x, duguje_x=duguje_x, potrazuje_x=potrazuje_x)


def _statement_seq(lines: list[list[OtpWord]]) -> str:
    for line in lines:
        texts = [word.text for word in line]
        for index, token in enumerate(texts):
            if not _fold(token).startswith('izvod'):
                continue
            rest = texts[index:]
            for offset, item in enumerate(rest):
                folded = _fold(item)
                if not folded.startswith('broj'):
                    continue
                inline = re.search(r'(\d+)', item)
                if inline and folded != 'broj' and not folded.startswith('broj'):
                    return inline.group(1)
                for nxt in rest[offset + 1:offset + 4]:
                    cleaned = nxt.strip(':')
                    if re.fullmatch(r'\d+', cleaned):
                        return cleaned
    raise ValueError('OTP IZV: nema broja izvoda.')


def _statement_day(lines: list[list[OtpWord]]) -> date:
    for line in lines:
        blob = _fold(_line_text(line))
        if 'promet' not in blob and 'dan:' not in blob and 'dan :' not in blob:
            continue
        for word in line:
            parsed = _parse_date(word.text)
            if parsed is not None:
                return parsed
    raise ValueError('OTP IZV: nema datuma izvoda.')


def _parse_date(token: str) -> date | None:
    match = _DATE_RE.fullmatch(token.strip())
    if not match:
        return None
    try:
        return datetime.strptime(token.strip(), '%d.%m.%Y').date()
    except ValueError:
        return None


def _is_account_header(line: list[OtpWord]) -> bool:
    if any(_RB_RE.fullmatch(word.text) for word in line):
        return False
    currencies = [
        word for word in line
        if word.text in ('EUR', 'HRK') and word.x < _LEFT_CURRENCY_X
    ]
    if not currencies:
        return False
    has_number = any(_DIGITS_RE.fullmatch(word.text) and word.x < _LEFT_CURRENCY_X for word in line)
    has_iban = any(_HR_IBAN_RE.fullmatch(word.text) for word in line)
    has_opening = 'pocetno' in _fold(_line_text(line))
    return has_number or has_iban or has_opening


def _parse_section(
    lines: list[list[OtpWord]],
    columns: _Columns,
    statement_number: str,
    statement_day: date,
) -> tuple[ParsedBankStatement | None, str | None]:
    header = lines[0]
    currency = next(word.text for word in header if word.text in ('EUR', 'HRK') and word.x < _LEFT_CURRENCY_X)
    iban = next((word.text for word in header if _HR_IBAN_RE.fullmatch(word.text)), '')
    account_number = _account_number(lines)
    if not iban:
        label = account_number or currency
        return None, (
            f'Izvod {statement_number}: preskočen račun {label} {currency} bez IBAN-a.'
        )

    opening = Decimal('0')
    closing = Decimal('0')
    transactions: list[ParsedBankTransaction] = []
    prefix: list[list[OtpWord]] = []
    current_rb: list[OtpWord] | None = None
    extras: list[list[OtpWord]] = []

    def finish(urb_line: list[OtpWord] | None) -> None:
        nonlocal current_rb, extras
        if current_rb is None:
            return
        tx = _transaction_from(
            rb_line=current_rb,
            context=prefix + extras + ([urb_line] if urb_line else []),
            urb_line=urb_line,
            columns=columns,
            statement_number=statement_number,
            currency=currency,
            statement_day=statement_day,
            own_iban=iban,
        )
        if tx is not None:
            transactions.append(tx)
        current_rb = None
        extras = []

    for line in lines:
        blob = _fold(_line_text(line))
        if 'pocetno' in blob:
            opening = _signed_balance(line, columns)
            continue
        if 'novo' in blob and 'stanje' in blob:
            finish(None)
            closing = _signed_balance(line, columns)
            prefix = []
            continue
        if 'ukupni' in blob or 'promet' in blob:
            continue
        if _is_tx_anchor(line):
            finish(None)
            current_rb = line
            extras = []
            continue
        if _is_urb_line(line):
            if current_rb is not None:
                finish(line)
                prefix = []
            continue
        if current_rb is not None:
            extras.append(line)
        else:
            prefix.append(line)

    finish(None)
    return (
        ParsedBankStatement(
            iban=iban,
            currency=currency,
            statement_number=statement_number,
            statement_date=statement_day,
            opening_balance=opening,
            closing_balance=closing,
            transactions=tuple(transactions),
        ),
        None,
    )


def _is_tx_anchor(line: list[OtpWord]) -> bool:
    has_rb = any(_RB_RE.fullmatch(word.text) for word in line)
    has_date = any(_parse_date(word.text) for word in line)
    return has_rb and has_date


def _parse_hr_amount(token: str) -> Decimal | None:
    if not _AMOUNT_RE.fullmatch(token):
        return None
    return Decimal(token.replace('.', '').replace(',', '.'))


def _signed_balance(line: list[OtpWord], columns: _Columns) -> Decimal:
    debit, credit = _column_amounts(line, columns)
    return credit - debit


def _column_amounts(line: list[OtpWord], columns: _Columns) -> tuple[Decimal, Decimal]:
    debit = Decimal('0')
    credit = Decimal('0')
    for word in line:
        amount = _parse_hr_amount(word.text)
        if amount is None:
            continue
        if word.x >= columns.boundary:
            credit = amount
        elif word.x >= columns.duguje_x - 30:
            debit = amount
    return debit, credit


def _transaction_from(
    *,
    rb_line: list[OtpWord],
    context: list[list[OtpWord]],
    urb_line: list[OtpWord] | None,
    columns: _Columns,
    statement_number: str,
    currency: str,
    statement_day: date,
    own_iban: str,
) -> ParsedBankTransaction | None:
    tx_date = statement_day
    for word in rb_line:
        parsed = _parse_date(word.text)
        if parsed is not None:
            tx_date = parsed
            break

    amount_line = urb_line or rb_line
    debit, credit = _column_amounts(amount_line, columns)
    if credit > 0 and debit == 0:
        amount, tx_type = credit, 'credit'
    elif debit > 0:
        amount, tx_type = debit, 'debit'
    elif credit > 0:
        amount, tx_type = credit, 'credit'
    else:
        return None

    external_id, reference = _urb_parts(urb_line)
    used_fallback = False
    if not external_id:
        used_fallback = True
        external_id = _fallback_external_id(
            statement_number=statement_number,
            currency=currency,
            tx_date=tx_date,
            amount=amount,
            tx_type=tx_type,
            counterparty_iban=own_iban,
            reference=reference,
        )

    return ParsedBankTransaction(
        external_id=external_id[:100],
        used_fallback_external_id=used_fallback,
        transaction_date=tx_date,
        value_date=tx_date,
        amount=amount,
        transaction_type=tx_type,
        description=_description(context, columns)[:500],
        reference=reference[:100],
        counterparty_name=_counterparty_name(rb_line, columns)[:200],
        counterparty_iban=_counterparty_iban(context, own_iban)[:34],
    )


def _counterparty_name(line: list[OtpWord], columns: _Columns) -> str:
    date_x = None
    for word in line:
        if _parse_date(word.text):
            date_x = word.x
            break
    parts: list[str] = []
    for word in line:
        if date_x is not None and word.x <= date_x:
            continue
        if word.x >= columns.opis_x - 10:
            continue
        if word.text in ('EUR', 'HRK') or _RB_RE.fullmatch(word.text):
            continue
        parts.append(word.text)
    return ' '.join(parts)


def _counterparty_iban(lines: list[list[OtpWord]], own_iban: str) -> str:
    for line in lines:
        for word in line:
            if _IBAN_RE.fullmatch(word.text) and word.text != own_iban:
                return word.text
    return ''


def _description(lines: list[list[OtpWord]], columns: _Columns) -> str:
    parts: list[str] = []
    for line in lines:
        for word in line:
            if word.x < columns.opis_x - 15 or word.x >= columns.duguje_x - 40:
                continue
            if word.text.upper() == 'URB':
                continue
            if re.fullmatch(r'\d+', word.text):
                continue
            if _parse_hr_amount(word.text) is not None or _RATE_RE.fullmatch(word.text):
                continue
            if _fold(word.text).rstrip(':') in ('pocetno', 'novo', 'stanje', 'ukupni', 'promet'):
                continue
            parts.append(word.text)
    return ' '.join(parts)


def _is_urb_line(line: list[OtpWord]) -> bool:
    return any(word.text.upper().startswith('URB') for word in line)


def _account_number(lines: list[list[OtpWord]]) -> str:
    for line in lines[:3]:
        for word in line:
            if _DIGITS_RE.fullmatch(word.text) and word.x < _LEFT_CURRENCY_X:
                return word.text
    return ''


def _urb_parts(line: list[OtpWord] | None) -> tuple[str, str]:
    if line is None:
        return '', ''
    numbers: list[str] = []
    seen = False
    for word in line:
        token = word.text
        if not seen:
            if token.upper().startswith('URB'):
                seen = True
                rest = token[3:]
                if rest.isdigit():
                    numbers.append(rest)
            continue
        if re.fullmatch(r'\d+', token):
            numbers.append(token)
        elif numbers:
            break
    if len(numbers) >= 2 and len(numbers[0]) <= 3:
        numbers[0] = numbers[0] + numbers[1]
        del numbers[1]
    external_id = numbers[0] if numbers else ''
    reference = numbers[1] if len(numbers) > 1 else ''
    return external_id, reference


def _fallback_external_id(
    *,
    statement_number: str,
    currency: str,
    tx_date: date,
    amount: Decimal,
    tx_type: str,
    counterparty_iban: str,
    reference: str,
) -> str:
    payload = '|'.join([
        statement_number,
        currency,
        tx_date.isoformat(),
        str(amount),
        tx_type,
        counterparty_iban,
        reference,
    ])
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()
