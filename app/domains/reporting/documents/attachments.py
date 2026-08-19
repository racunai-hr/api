"""Safe attachment download helpers. Never leak storage paths."""

from __future__ import annotations

import errno
from pathlib import PureWindowsPath

from domains.reporting.documents.provenance import provenanced

ATTACHMENT_CONTENT_UNAVAILABLE = {
    'code': 'attachment_content_unavailable',
    'detail': 'Attachment content is unavailable.',
}

MISSING_STORAGE_ERRNOS = frozenset({errno.ENOENT, errno.ENOTDIR, errno.EISDIR})


def is_missing_storage_error(exc: BaseException) -> bool:
    if isinstance(exc, FileNotFoundError):
        return True
    if isinstance(exc, IsADirectoryError):
        return True
    if isinstance(exc, OSError):
        return getattr(exc, 'errno', None) in MISSING_STORAGE_ERRNOS
    return False


def attachment_blob_available(attachment) -> bool:
    field = getattr(attachment, 'file', None)
    if not field or not field.name:
        return False
    try:
        return bool(field.storage.exists(field.name))
    except OSError as exc:
        if is_missing_storage_error(exc):
            return False
        raise


def download_available_field(attachment) -> dict:
    if attachment_blob_available(attachment):
        return provenanced(True, source='storage')
    return {'value': False, 'reason': 'not_recorded', 'source': 'storage'}


def safe_download_filename(name: str | None) -> str:
    raw = (name or '').replace('\r', '').replace('\n', '')
    base = PureWindowsPath(raw).name.strip().strip('"')
    if not base or base in {'.', '..'}:
        return 'attachment.bin'
    return base
