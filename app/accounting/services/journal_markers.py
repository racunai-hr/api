import re

from accounting.models import PostingRule

DOCUMENT_MARKER_PREFIX = '['
DOCUMENT_MARKER_SUFFIX = ']'
DOCUMENT_MARKER_PATTERN = re.compile(r'^\[([a-z_]+)\]', re.IGNORECASE)

MANUAL_DOCUMENT_TYPE = 'manual'
MANUAL_DOCUMENT_TYPE_LABEL = 'Ručno / ostalo'

DOCUMENT_TYPE_LABELS = dict(PostingRule.DOCUMENT_TYPES)


def iter_document_types():
    yield from PostingRule.DOCUMENT_TYPES


def has_document_marker(description: str) -> bool:
    return extract_document_type(description) is not None


def extract_document_type(description: str) -> str | None:
    if not description:
        return None
    match = DOCUMENT_MARKER_PATTERN.match(description)
    if not match:
        return None
    key = match.group(1).lower()
    return key if key in DOCUMENT_TYPE_LABELS else None


def document_type_label(description: str) -> str:
    key = extract_document_type(description)
    return DOCUMENT_TYPE_LABELS[key] if key else MANUAL_DOCUMENT_TYPE_LABEL


def build_document_marker(document_type: str) -> str:
    return f'{DOCUMENT_MARKER_PREFIX}{document_type}{DOCUMENT_MARKER_SUFFIX}'
