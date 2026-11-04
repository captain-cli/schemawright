"""Phase 6C.2: conservative recovery reconciliation for a single-column CREATE TABLE.

May only insert a *missing history row* following independently queried schema
checks, identical to a very restricted SQL template. Does not execute DDL,
change tables, delete rows, or modify an existing migration history record.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Callable

from .config import DatabaseConfig
from .configwright_plan import plan_database
from .files import checksum
from .migration_apply import _IDENT, _parse_create_table
from .owner_inspect import _local_host


class ReconcileError(ValueError):
    """Safe-to-display exception without SQL driver errors or secrets."""


# Deliberately NOT a general SQL parser. Reject unknown table definitions.
_SINGLE_COLUMN = re.compile(
    r"\ACREATE\s+TABLE\s+`?(?P<table>[A-Za-z_][A-Za-z0-9_]*)`?\s*"
    r"\(\s*`?(?P<column>[A-Za-z_][A-Za-z0-9_]*)`?\s+"
    r"(?P<type>BIGINT|INT|INTEGER|SMALLINT|TINYINT)\s+"
    r"(?:(?:NOT\s+NULL)\s+)?PRIMARY\s+KEY\s*\)\s*"
    r"ENGINE\s*=\s*InnoDB\s*;?\s*\Z", re.IGNORECASE)
_TYPES = {'INTEGER': 'int', 'INT': 'int', 'BIGINT': 'bigint',
          'SMALLINT': 'smallint', 'TINYINT': 'tinyint'}


def _expected(sql: str) -> tuple[str, str, str]:
    # Reject SQL comment, multiple statements and other risky tokens.
    table = _parse_create_table(sql)
    if any(token in sql for token in ('--', '/*', '*/', '#', '\x00')):
        raise ReconcileError('Unsupported SQL syntax; manual review required')
    matched = _SINGLE_COLUMN.fullmatch(sql.strip())
    if not matched or matched.group('table') != table:
        raise ReconcileError('Only a one-column integer PRIMARY KEY InnoDB CREATE TABLE can be reconciled automatically')
    return table, matched.group('column'), _TYPES[matched.group('type').upper()]


def reconcile_one(db: DatabaseConfig, *, username: str, password: str, filename: str,
                  approved: bool = False, dry_run: bool = True,
                  connect: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Strictly verify a missing history row and optionally insert it.

    Uses the same advisory lock as the 6B executor. A competing non-SchemaWright
    DDL writer may still race; do not use where external DDL is uncontrolled.
    """
    opts = db.settings
    host, port, schema = opts.get('host', 'localhost'), opts.get('port', 3306), opts.get('database')
    if (db.driver.lower() != 'mysql' or not isinstance(host, str) or not _local_host(host)
            or not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535
            or not isinstance(schema, str) or not _IDENT.fullmatch(schema)):
        raise ReconcileError('Only local MySQL with a valid schema is supported')
    if (not isinstance(username, str) or not _IDENT.fullmatch(username)
            or username.lower() in {'root', 'schemawright_app'} or not password):
        raise ReconcileError('A dedicated migrator and its password are required')
    if not dry_run and not approved:
        raise ReconcileError('Explicit approval required')
    if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith('.sql'):
        raise ReconcileError('Provide one migration filename, not a path')

    entries, issues = plan_database(db)
    if issues:
        raise ReconcileError('Migration plan has issues; resolve them before reconciliation')
    active = [e for e in entries if e['stage'] == 'active' and e['kind'] == 'migration']
    matches = [e for e in active if Path(e['file']).name == filename]
    if len(matches) != 1:
        raise ReconcileError('Migration must exist exactly once among active V files')
    e = matches[0]
    p = Path(e['file'])
    if p.is_symlink() or not p.is_file() or p.stat().st_size > 256_000:
        raise ReconcileError('Unsafe or missing migration file')
    sql = p.read_text(encoding='utf-8')
    table, column, type_name = _expected(sql)
    digest = checksum(p)
    key = ('V', int(e['version']), int(e['order']))

    if connect is None:
        try:
            import pymysql
        except ImportError:
            raise ReconcileError('Install schemawright[mysql]') from None
        connect = pymysql.connect
    conn = None
    locked = False
    lock_name = 'schemawright:migrate:' + hashlib.sha256(schema.encode()).hexdigest()[:32]
    try:
        conn = connect(host=host, port=port, user=username, password=password,
                       database=schema, connect_timeout=5, read_timeout=5,
                       write_timeout=5, autocommit=True, charset='utf8mb4')
        with conn.cursor() as cur:
            # Acquire lock in both dry-run and apply to avoid racing SchemaWright migrations.
            cur.execute('SELECT GET_LOCK(%s,%s)', (lock_name, 5))
            if cur.fetchone() != (1,):
                raise ReconcileError('Could not acquire migration advisory lock')
            locked = True
            cur.execute('SELECT type,version,install_order,filename,checksum,success FROM `schema_migrations`')
            records = cur.fetchall()
            recorded = {}
            for kind, ver, order, name, hash_value, success in records:
                rk = (str(kind).upper(), int(ver), int(order))
                if rk in recorded:
                    raise ReconcileError('Duplicate migration history identity')
                recorded[rk] = (name, hash_value, success)
            if key in recorded:
                raise ReconcileError('Migration already has history; never overwrite or repair existing records')
            # Require all other history rows consistent with active local files.
            local = {( 'V', int(f['version']), int(f['order'])): Path(f['file'])
                     for f in active}
            if len(local) != len(active):
                raise ReconcileError('Duplicate local migration identities')
            for rk, (name, hash_value, success) in recorded.items():
                lp = local.get(rk)
                if lp is None or lp.is_symlink() or not lp.is_file() or success != 1 or lp.name != name or checksum(lp) != hash_value:
                    raise ReconcileError('Existing migration history has drift; reconciliation blocked')
            # Verify ENGINE, BASE TABLE, single column, signed integer type,
            # non-null, primary key, no default/extra, and only one index.
            cur.execute('SELECT TABLE_TYPE,ENGINE FROM information_schema.TABLES '
                        'WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s', (schema, table))
            rows = cur.fetchall()
            if list(rows) != [('BASE TABLE', 'InnoDB')]:
                raise ReconcileError('Table missing, non-InnoDB, or ambiguous')
            cur.execute('SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLUMN_KEY,COLUMN_DEFAULT,EXTRA '
                        'FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s '
                        'ORDER BY ORDINAL_POSITION', (schema, table))
            columns = cur.fetchall()
            if list(columns) != [(column, type_name, 'NO', 'PRI', None, '')]:
                raise ReconcileError('Actual table columns differ from migration; manual inspection required')
            cur.execute('SELECT INDEX_NAME,NON_UNIQUE,COLUMN_NAME,SEQ_IN_INDEX '
                        'FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s '
                        'ORDER BY INDEX_NAME,SEQ_IN_INDEX', (schema, table))
            if list(cur.fetchall()) != [('PRIMARY', 0, column, 1)]:
                raise ReconcileError('Index/primary key mismatch')
            cur.execute('SELECT CONSTRAINT_TYPE FROM information_schema.TABLE_CONSTRAINTS '
                        'WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s', (schema, table))
            if list(cur.fetchall()) != [('PRIMARY KEY',)]:
                raise ReconcileError('Unsupported constraints present')
            cur.execute('SELECT COUNT(*) FROM information_schema.TRIGGERS '
                        'WHERE EVENT_OBJECT_SCHEMA=%s AND EVENT_OBJECT_TABLE=%s', (schema, table))
            if cur.fetchone() != (0,):
                raise ReconcileError('Table has triggers; manual review required')
            # Column defaults and indexes match, and the table must be empty.
            cur.execute(f'SELECT COUNT(*) FROM `{table}`')
            if cur.fetchone() != (0,):
                raise ReconcileError('Table contains data; manual review required')
            if p.is_symlink() or checksum(p) != digest or p.read_text(encoding='utf-8') != sql:
                raise ReconcileError('Migration file changed while inspecting; reconciliation blocked')
            outcome = {'database': db.name, 'target': schema, 'migration': filename,
                       'createsTable': table, 'structureVerified': True,
                       'historyMissing': True, 'changesExecuted': False,
                       'action': 'would-record-history'}
            if dry_run:
                return outcome
            # No DDL, only the history INSERT; unique primary key stops duplicates.
            cur.execute('INSERT INTO `schema_migrations` '
                        '(type,version,install_order,description,filename,checksum,execution_ms,success) '
                        'VALUES (%s,%s,%s,%s,%s,%s,NULL,1)',
                        ('V', key[1], key[2], p.name.rsplit('__', 1)[-1][:-4], p.name, digest))
            outcome['action'] = 'recorded-history'
            outcome['changesExecuted'] = True
            return outcome
    except ReconcileError:
        raise
    except Exception:
        raise ReconcileError('Reconciliation aborted; inspect privileges, table definition and migration history') from None
    finally:
        if conn is not None:
            if locked:
                try:
                    with conn.cursor() as cur:
                        cur.execute('SELECT RELEASE_LOCK(%s)', (lock_name,))
                except Exception:
                    pass
            try:
                conn.close()
            except Exception:
                pass
