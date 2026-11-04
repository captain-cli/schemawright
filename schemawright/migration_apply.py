"""Phase 6B1: explicitly approved, one-at-a-time MySQL CREATE TABLE migrations.

MySQL DDL auto-commits. The history record is inserted *after* CREATE TABLE;
if that second step fails, stop and investigate before retrying. This is not a
transactional or general-purpose SQL migration executor.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import time
from typing import Any, Callable

from .config import DatabaseConfig
from .configwright_plan import plan_database
from .files import checksum
from .owner_inspect import _local_host


class MigrationApplyError(ValueError):
    """Error messages contain no connection or credential values."""


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
_CREATE = re.compile(r"\s*CREATE\s+TABLE\s+`?(?P<table>[A-Za-z_][A-Za-z0-9_]*)`?\s*\(", re.I)


def _parse_create_table(sql: str) -> str:
    """Conservative guardrail, not a complete SQL parser.

    Phase 6B1 intentionally supports only simple, single-statement CREATE TABLE
    scripts with no SQL comments. All other SQL requires later review tooling.
    """
    text = sql.strip()
    if not text or any(token in text for token in ('--', '/*', '*/', '#', '\\x00')):
        raise MigrationApplyError('Only uncommented CREATE TABLE SQL is supported in Phase 6B1')
    body = text[:-1].strip() if text.endswith(';') else text
    if ';' in body:
        raise MigrationApplyError('Multiple SQL statements are not supported')
    match = _CREATE.match(body)
    if not match or not _IDENT.fullmatch(match.group('table')):
        raise MigrationApplyError('Only an unqualified CREATE TABLE statement is supported')
    if not body.endswith(')') and not re.search(r'\)\s+ENGINE\s*=\s*InnoDB\s*\Z', body, re.I):
        raise MigrationApplyError('CREATE TABLE must end with a closing parenthesis or ENGINE=InnoDB')
    if re.search(r'\b(IF\s+NOT\s+EXISTS|AS\s+SELECT|LIKE|TEMPORARY|REPLACE)\b', body, re.I):
        raise MigrationApplyError('CREATE TABLE shortcuts, copies and temporary tables are not supported')
    return match.group('table')


def migrate_one(db: DatabaseConfig, *, username: str, password: str,
                approved: bool = False, dry_run: bool = True,
                connect: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Preflight or apply exactly the next pending active V migration.

    Executes a single CREATE TABLE only after target, history, checksum, table
    absence, identity and file stability checks. No automatic retries.
    """
    if db.driver.lower() != 'mysql':
        raise MigrationApplyError('Only MySQL is supported in Phase 6B1')
    opts = db.settings
    host, port, schema = opts.get('host', 'localhost'), opts.get('port', 3306), opts.get('database')
    if not isinstance(host, str) or not _local_host(host):
        raise MigrationApplyError('Only loopback MySQL hosts are supported')
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise MigrationApplyError('Invalid MySQL port')
    if not isinstance(schema, str) or not _IDENT.fullmatch(schema):
        raise MigrationApplyError('Invalid target database name')
    if not isinstance(username, str) or not _IDENT.fullmatch(username) or username.lower() in {'root', 'schemawright_app'}:
        raise MigrationApplyError('Dedicated migrator identity required')
    if not password:
        raise MigrationApplyError('Migrator password required')
    if not dry_run and not approved:
        raise MigrationApplyError('Explicit migration approval required')

    entries, issues = plan_database(db)
    if issues:
        raise MigrationApplyError('Invalid or duplicate SQL file names; inspect offline migration plan')
    active = [x for x in entries if x['stage'] == 'active']
    if any(x['kind'] not in {'migration', 'rollback'} for x in active):
        raise MigrationApplyError('Only versioned V migrations are supported for execution in Phase 6B1')
    active = [x for x in active if x['kind'] == 'migration']
    active.sort(key=lambda x: (x['version'], x['order'], x['file']))
    keys = [('V', int(e['version']), int(e['order'])) for e in active]
    if len(keys) != len(set(keys)):
        raise MigrationApplyError('Duplicate active migration identifiers')
    # Read and validate every active SQL file BEFORE opening a database connection.
    files = []
    for entry, key in zip(active, keys):
        p = Path(entry['file'])
        if p.is_symlink() or not p.is_file() or p.stat().st_size > 256_000:
            raise MigrationApplyError('Migration file is missing, linked, or too large')
        sql = p.read_text(encoding='utf-8')
        table = _parse_create_table(sql)
        files.append((entry, key, p, checksum(p), sql, table))

    if connect is None:
        try:
            import pymysql
        except ImportError:
            raise MigrationApplyError('Install schemawright[mysql] to connect to MySQL') from None
        connect = pymysql.connect

    connection = None
    locked = False
    ddl_attempted = False
    lock_name = 'schemawright:migrate:' + hashlib.sha256(schema.encode()).hexdigest()[:32]
    try:
        connection = connect(host=host, port=port, user=username, password=password,
                             database=schema, connect_timeout=5, read_timeout=5,
                             write_timeout=5, autocommit=True, charset='utf8mb4')
        with connection.cursor() as cur:
            if not dry_run:
                cur.execute('SELECT GET_LOCK(%s, %s)', (lock_name, 5))
                if cur.fetchone() != (1,):
                    raise MigrationApplyError('Cannot acquire migration execution lock')
                locked = True
            cur.execute('SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s',
                        (schema, 'schema_migrations'))
            row = cur.fetchone()
            if not row or row[0] != 1:
                raise MigrationApplyError('Migration history is missing or ambiguous; initialize it first')
            cur.execute('SELECT type,version,install_order,filename,checksum,success FROM `schema_migrations`')
            history = {}
            for kind, ver, order, name, recorded_hash, success in cur.fetchall():
                key = (str(kind).upper(), int(ver), int(order))
                if key in history:
                    raise MigrationApplyError('Duplicate migration history')
                history[key] = (name, recorded_hash, success)
            local = {x[1]: x for x in files}
            for key, (name, recorded_hash, success) in history.items():
                if key not in local:
                    raise MigrationApplyError('Database records an unknown or removed migration; manual review required')
                e, _, p, current_hash, _, _ = local[key]
                if success != 1 or name != p.name or recorded_hash != current_hash:
                    raise MigrationApplyError('Migration history mismatch or recorded failure; manual review required')
            pending = [x for x in files if x[1] not in history]
            if not pending:
                return {'database': db.name, 'target': schema, 'action': 'up-to-date',
                        'pending': 0, 'changesExecuted': False}
            entry, key, p, file_hash, sql, table = pending[0]
            cur.execute('SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s',
                        (schema, table))
            row = cur.fetchone()
            if not row or row[0] != 0:
                raise MigrationApplyError('Target migration table already exists; possible interrupted run; manual review required')
            if dry_run:
                return {'database': db.name, 'target': schema, 'action': 'would-apply',
                        'migration': p.name, 'createsTable': table,
                        'pending': len(pending), 'changesExecuted': False}
            if p.is_symlink() or checksum(p) != file_hash or p.read_text(encoding='utf-8') != sql:
                raise MigrationApplyError('Migration file changed during preflight; execution refused')
            ddl_attempted = True
            start = time.monotonic()
            cur.execute(sql)
            elapsed = int((time.monotonic() - start) * 1000)
            # DDL has already committed at this point. Record afterwards.
            cur.execute('INSERT INTO `schema_migrations` '
                        '(type,version,install_order,description,filename,checksum,execution_ms,success) '
                        'VALUES (%s,%s,%s,%s,%s,%s,%s,1)',
                        ('V', key[1], key[2], p.name.rsplit('__', 1)[-1][:-4], p.name, file_hash, elapsed))
            return {'database': db.name, 'target': schema, 'action': 'applied',
                    'migration': p.name, 'createsTable': table,
                    'pendingBefore': len(pending), 'changesExecuted': True}
    except MigrationApplyError:
        raise
    except Exception:
        if ddl_attempted:
            raise MigrationApplyError('Migration CREATE TABLE was attempted; inspect actual schema and history manually before retrying') from None
        raise MigrationApplyError('Migration preflight failed; verify history, SQL and migrator privileges') from None
    finally:
        if connection is not None:
            if locked:
                try:
                    with connection.cursor() as cur:
                        cur.execute('SELECT RELEASE_LOCK(%s)', (lock_name,))
                except Exception:
                    pass
            try:
                connection.close()
            except Exception:
                pass
