from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass

from banking.importers.base import BankStatementImportResult, ImportResultBuilder
from banking.importers.camt053_importer import Camt053Importer
from banking.parsers.camt053 import parse_camt053
from banking.parsers.format_detection import detect_format

logger = logging.getLogger(__name__)

IMPORTERS = {
    'camt053': Camt053Importer,
}


def import_bank_statement_file(
    *,
    tenant,
    user,
    content: bytes | str,
    filename: str = '',
) -> BankStatementImportResult:
    import_id = str(uuid.uuid4())
    started = time.monotonic()
    fmt = detect_format(content, filename)

    logger.info(
        'bank_statement_import_started',
        extra={
            'import_id': import_id,
            'tenant_id': tenant.pk,
            'user_id': user.pk,
            'import_filename': filename,
            'format': fmt,
        },
    )

    importer_cls = IMPORTERS.get(fmt)
    if importer_cls is None:
        duration_ms = int((time.monotonic() - started) * 1000)
        result = _error_result(
            import_id=import_id,
            fmt=fmt,
            message=f'Nepodržan format: {fmt}',
            duration_ms=duration_ms,
        )
        _log_import_result(tenant, user, filename, result)
        return result

    try:
        parsed = _parse_for_format(fmt, content)
    except Exception as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        result = _error_result(
            import_id=import_id,
            fmt=fmt,
            message=str(exc),
            duration_ms=duration_ms,
        )
        _log_import_result(tenant, user, filename, result)
        return result

    builder = importer_cls().import_parsed(
        tenant=tenant,
        user=user,
        statements=parsed,
        import_id=import_id,
    )
    duration_ms = int((time.monotonic() - started) * 1000)
    result = builder.build(format=fmt, duration_ms=duration_ms, import_id=import_id)
    _log_import_result(tenant, user, filename, result)
    return result


def _parse_for_format(fmt: str, content: bytes | str):
    if fmt == 'camt053':
        return parse_camt053(content)
    raise ValueError(f'Parser nije implementiran za format: {fmt}')


def _error_result(*, import_id: str, fmt: str, message: str, duration_ms: int) -> BankStatementImportResult:
    builder = ImportResultBuilder()
    builder.add_error(message)
    return builder.build(format=fmt, duration_ms=duration_ms, import_id=import_id)


def _log_import_result(tenant, user, filename: str, result: BankStatementImportResult) -> None:
    logger.info(
        'bank_statement_import_completed',
        extra={
            'import_id': result.import_id,
            'tenant_id': tenant.pk,
            'user_id': user.pk,
            'import_filename': filename,
            'format': result.format,
            'statements_processed': result.statements_processed,
            'statements_created': result.statements_created,
            'statements_updated': result.statements_updated,
            'transactions_processed': result.transactions_processed,
            'transactions_created': result.transactions_created,
            'transactions_skipped': result.transactions_skipped,
            'warnings_count': len(result.warnings),
            'errors_count': len(result.errors),
            'duration_ms': result.duration_ms,
        },
    )
