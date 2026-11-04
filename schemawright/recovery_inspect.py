"""Phase 6C1: read-only recovery analysis for single CREATE TABLE migrations.

Never marks migrations applied or retries DDL. An existing target without
recorded history is an ambiguity requiring separate human schema verification.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Callable
import re

from .config import DatabaseConfig
from .configwright_plan import plan_database
from .files import checksum
from .migration_apply import _parse_create_table, _IDENT
from .owner_inspect import _local_host


class RecoveryInspectError(ValueError):
    """Credential-safe failure, without raw connector output."""


def inspect_recovery(db: DatabaseConfig, *, username: str, password: str,
                     connect: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Compare active V files, history records, and actual target-table existence.

    The report is diagnostic only. Even apparently matching tables cannot be
    automatically reconciled: table name/existence does NOT prove schema parity.
    """
    s = db.settings
    host, port, schema = s.get('host', 'localhost'), s.get('port', 3306), s.get('database')
    if db.driver.lower() != 'mysql' or not isinstance(host, str) or not _local_host(host):
        raise RecoveryInspectError('Recovery inspection supports local MySQL only')
    if (not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535
            or not isinstance(schema, str) or not _IDENT.fullmatch(schema)
            or not isinstance(username, str) or not username or not password):
        raise RecoveryInspectError('Invalid target or credentials')

    entries, issues = plan_database(db)
    active = [e for e in entries if e['stage'] == 'active' and e['kind'] == 'migration']
    unsupported = [e for e in entries if e['stage'] == 'active' and e['kind'] not in {'migration', 'rollback'}]
    if unsupported:
        issues.append('Other active SQL kinds are not supported by the Phase 6C1 recovery inspector')
    local = {}
    for e in active:
        path = Path(e['file'])
        key = ('V', int(e['version']), int(e['order']))
        if key in local:
            issues.append('Duplicate local migration identity')
            continue
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 256_000:
            issues.append('Missing, linked or oversized migration file')
            continue
        try:
            table = _parse_create_table(path.read_text(encoding='utf-8'))
            digest = checksum(path)
        except (OSError, UnicodeError, ValueError):
            issues.append('Migration cannot be parsed or hashed safely')
            continue
        local[key] = {'file': path.name, 'table': table, 'checksum': digest}

    if connect is None:
        try:
            import pymysql
        except ImportError:
            raise RecoveryInspectError('Install schemawright[mysql]') from None
        connect = pymysql.connect
    connection = None
    try:
        connection = connect(host=host, port=port, user=username, password=password,
                             database=schema, connect_timeout=5, read_timeout=5,
                             write_timeout=5, autocommit=False, charset='utf8mb4')
        with connection.cursor() as cursor:
            cursor.execute('SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s', (schema,))
            present = {str(r[0]) for r in cursor.fetchall()}
            history_exists = 'schema_migrations' in present
            if history_exists:
                cursor.execute('SELECT type,version,install_order,filename,checksum,success FROM `schema_migrations`')
                rows = cursor.fetchall()
            else:
                rows = []
        history = {}
        for kind, version, order, filename, digest, success in rows:
            key = (str(kind).upper(), int(version), int(order))
            if key in history:
                issues.append('Duplicate history identity')
            history[key] = (filename, digest, success)
        findings = []
        for key, item in sorted(local.items()):
            recorded = history.pop(key, None)
            target_exists = item['table'] in present
            if not history_exists:
                state = 'history_unknown'
            elif recorded is None:
                state = 'unrecorded_table' if target_exists else 'pending'
            elif recorded[2] != 1:
                state = 'failed_history'
            elif recorded[0] != item['file']:
                state = 'filename_drift'
            elif recorded[1] != item['checksum']:
                state = 'checksum_drift'
            elif not target_exists:
                state = 'missing_target_table'
            else:
                state = 'applied'
            findings.append({'type': key[0], 'version': key[1], 'order': key[2],
                             'file': item['file'], 'createsTable': item['table'],
                             'tableExists': target_exists, 'state': state})
        for key, (filename, _digest, _success) in sorted(history.items()):
            findings.append({'type': key[0], 'version': key[1], 'order': key[2],
                             'file': str(filename), 'createsTable': None,
                             'tableExists': None, 'state': 'missing_local'})
        counts = dict(sorted(Counter(f['state'] for f in findings).items()))
        blocked = bool(issues or not history_exists or any(f['state'] not in ('pending', 'applied') for f in findings))
        return {'database': db.name, 'target': schema, 'historyTableExists': history_exists,
                'readOnly': True, 'changesExecuted': False, 'safeToProceed': not blocked,
                'reconciliationPerformed': False, 'items': findings,
                'counts': counts, 'issues': issues}
    except RecoveryInspectError:
        raise
    except Exception:
        raise RecoveryInspectError('Recovery inspection failed; check access, history schema and connection') from None
    finally:
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass
            try:
                connection.close()
            except Exception:
                pass
