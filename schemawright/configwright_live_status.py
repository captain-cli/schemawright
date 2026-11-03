"""MySQL-only read-only comparison of active migration files and recorded history."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import ssl
from typing import Any, Callable

from .config import DatabaseConfig
from .configwright_plan import plan_database
from .files import checksum
from .owner_inspect import _local_host


class LiveStatusError(ValueError):
    """Credential-safe error message."""


def migration_status(db: DatabaseConfig, *, username: str, password: str,
                     connect: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Read existing history only. Never issue DDL, DML, or initialize the history table."""
    if db.driver.lower() != 'mysql':
        raise LiveStatusError('Live migration status currently supports MySQL/MariaDB only')
    host, port, database = db.settings.get('host', 'localhost'), db.settings.get('port', 3306), db.settings.get('database')
    if (not isinstance(host, str) or not host.strip() or not isinstance(database, str)
            or not database.strip() or isinstance(port, bool) or not isinstance(port, int)
            or not 1 <= port <= 65535 or not username or not password):
        raise LiveStatusError('Invalid connection settings or missing authentication')
    if not database.replace('_', 'a').isalnum() or len(database) > 64:
        raise LiveStatusError('Unsupported database identifier')

    entries, issues = plan_database(db)
    active = [e for e in entries if e['stage'] == 'active' and e['kind'] != 'rollback']
    # Prefix mapping: migration V, seed S, procedure P, trigger T.
    prefix = {'migration': 'V', 'procedure': 'P', 'trigger': 'T', 'seed': 'S'}
    active_keys = [(prefix[e['kind']], e['version'], e['order']) for e in active]
    if len(active_keys) != len(set(active_keys)):
        issues.append('Duplicate active migration identity across collections')

    if connect is None:
        try:
            import pymysql
        except ImportError:
            raise LiveStatusError('Missing MySQL connector; install schemawright[mysql]') from None
        connect = pymysql.connect
    connection = None
    try:
        options = dict(host=host, port=port, user=username, password=password,
                       database=database, connect_timeout=5, read_timeout=5, write_timeout=5,
                       autocommit=False, charset='utf8mb4')
        if not _local_host(host):
            options['ssl'] = ssl.create_default_context()
        connection = connect(**options)
        with connection.cursor() as cursor:
            if not _local_host(host):
                cursor.execute('SHOW STATUS LIKE %s', ('Ssl_cipher',))
                ssl_row = cursor.fetchone()
                if not ssl_row or len(ssl_row) < 2 or not ssl_row[1]:
                    raise LiveStatusError('Remote MySQL connection lacks verified TLS')
            cursor.execute('SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s',
                           (database, 'schema_migrations'))
            row = cursor.fetchone()
            if not row:
                raise LiveStatusError('Unable to establish migration-history table presence')
            exists = bool(row[0])
            history = []
            if exists:
                cursor.execute('SELECT type, version, install_order, filename, checksum, success '
                               'FROM `schema_migrations`')
                history = cursor.fetchall()

        result = []
        recorded: dict[tuple[str, int, int], tuple] = {}
        for row in history:
            kind, version, order, filename, hash_value, success = row
            key = str(kind).upper(), int(version), int(order)
            if key in recorded:
                issues.append('Duplicate applied-history identity')
            recorded[key] = (filename, hash_value, success)
        for e, key in zip(active, active_keys):
            state = 'history_unknown' if not exists else 'pending'
            recorded_hash = None
            if key in recorded:
                filename, recorded_hash, success = recorded.pop(key)
                if not success:
                    state = 'failed'
                elif filename != Path(e['file']).name:
                    state = 'filename_drift'
                elif not isinstance(recorded_hash, str) or checksum(Path(e['file'])) != recorded_hash:
                    state = 'checksum_drift'
                else:
                    state = 'applied'
            result.append({'type': key[0], 'version': key[1], 'order': key[2],
                           'file': str(e['file']), 'state': state})
        for (kind, version, order), row in sorted(recorded.items()):
            result.append({'type': kind, 'version': version, 'order': order,
                           'file': str(row[0]), 'state': 'missing_local'})
        counts = dict(sorted(Counter(e['state'] for e in result).items()))
        blocked = bool(issues or any(e['state'] in {'failed', 'filename_drift', 'checksum_drift', 'missing_local'} for e in result))
        return {'database': db.name, 'target': database, 'historyTableExists': exists,
                'readOnly': True, 'items': result, 'counts': counts, 'issues': issues,
                'safeToPlan': exists and not blocked, 'changesExecuted': False}
    except LiveStatusError:
        raise
    except Exception:
        # Driver exceptions may contain user/password/host details. Never echo them.
        raise LiveStatusError('Read-only migration inspection failed; verify credentials, privileges, history schema and connection') from None
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
