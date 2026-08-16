from __future__ import annotations

import subprocess
from pathlib import Path

from django.db import connection
from django.db.migrations.recorder import MigrationRecorder


def get_git_sha() -> str:
    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return 'unknown'


def get_migration_version(app_label: str = 'banking') -> str:
    applied = MigrationRecorder.Migration.objects.filter(app=app_label).order_by('id')
    latest = applied.last()
    if latest is None:
        return f'{app_label}.0000'
    return f'{app_label}.{latest.name}'


def get_audit_metadata() -> dict[str, str]:
    return {
        'git_sha': get_git_sha(),
        'migration_version': get_migration_version('banking'),
        'database': connection.settings_dict.get('NAME', ''),
    }
