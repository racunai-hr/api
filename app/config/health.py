from __future__ import annotations

from django.db import connection
from django.http import JsonResponse


def _check_database() -> tuple[bool, str]:
    try:
        connection.ensure_connection()
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        return True, 'ok'
    except Exception as exc:
        return False, str(exc)


def _check_celery_broker() -> tuple[bool, str]:
    try:
        from celery import current_app

        conn = current_app.connection()
        conn.ensure_connection(max_retries=1)
        conn.release()
        return True, 'ok'
    except Exception as exc:
        return False, str(exc)


def ready_view(request):
    """Readiness probe: DB + Celery broker (M1.8 load/ops)."""
    checks = {
        'database': _check_database()[1],
        'celery_broker': _check_celery_broker()[1],
    }
    ready = all(
        checks[key] == 'ok'
        for key in ('database', 'celery_broker')
    )
    status_code = 200 if ready else 503
    return JsonResponse(
        {'status': 'ready' if ready else 'not_ready', 'checks': checks},
        status=status_code,
    )
