"""Phase 6D.1: fail-closed ordered batch of simple MySQL CREATE TABLE migrations.

A single advisory lock protects the whole batch from cooperating SchemaWright writers.
MySQL DDL auto-commits: if a DDL or history write fails, stop and inspect manually.
No automatic retry, rollback or reconciliation. No production host support.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import time
from typing import Any, Callable

from .config import DatabaseConfig
from .configwright_plan import plan_database
from .files import checksum
from .migration_apply import _IDENT, _parse_create_table
from .owner_inspect import _local_host


class MigrationBatchError(ValueError):
    """Safe failure; completed items denote verified history inserts only."""
    def __init__(self, message: str, completed: list[str] | None = None):
        super().__init__(message)
        self.completed = list(completed or [])


def migrate_batch(db: DatabaseConfig, *, username: str, password: str,
                  approved: bool = False, dry_run: bool = True,
                  max_migrations: int = 10,
                  connect: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Validate all pending V migrations, then apply at most max_migrations.

    All pending files are preflighted, including targets beyond the batch limit.
    Applied rows must form a prefix of the sorted migration sequence.
    """
    opts = db.settings
    host, port, schema = opts.get('host', 'localhost'), opts.get('port', 3306), opts.get('database')
    if (db.driver.lower() != 'mysql' or not isinstance(host, str) or not _local_host(host)
            or not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535
            or not isinstance(schema, str) or not _IDENT.fullmatch(schema)):
        raise MigrationBatchError('Only valid local MySQL targets are supported')
    if (not isinstance(username, str) or not _IDENT.fullmatch(username)
            or username.lower() in {'root', 'schemawright_app'} or not password):
        raise MigrationBatchError('A dedicated migrator login and password are required')
    if not isinstance(max_migrations, int) or isinstance(max_migrations, bool) or not 1 <= max_migrations <= 25:
        raise MigrationBatchError('Batch limit must be between 1 and 25')
    if not dry_run and not approved:
        raise MigrationBatchError('Explicit batch authorization required')

    entries, issues = plan_database(db)
    if issues:
        raise MigrationBatchError('Migration plan contains invalid or duplicate files')
    active = [e for e in entries if e['stage'] == 'active']
    if any(e['kind'] not in ('migration', 'rollback') for e in active):
        raise MigrationBatchError('Only active V CREATE TABLE migrations are supported')
    active = sorted((e for e in active if e['kind'] == 'migration'),
                    key=lambda e: (e['version'], e['order'], e['file']))
    files = []
    identities, targets = set(), set()
    for e in active:
        p = Path(e['file'])
        key = ('V', int(e['version']), int(e['order']))
        if key in identities:
            raise MigrationBatchError('Duplicate active migration identity')
        identities.add(key)
        if p.is_symlink() or not p.is_file() or p.stat().st_size > 256_000:
            raise MigrationBatchError('Unsafe or missing migration file')
        try:
            sql = p.read_text(encoding='utf-8')
            table = _parse_create_table(sql)
            digest = checksum(p)
        except (ValueError, UnicodeError, OSError):
            raise MigrationBatchError('Migration contains unsupported SQL or cannot be read') from None
        if table.lower() in targets or table.lower() == 'schema_migrations':
            raise MigrationBatchError('Duplicate or reserved CREATE TABLE target')
        targets.add(table.lower())
        files.append((key, p, sql, digest, table))

    if connect is None:
        try:
            import pymysql
        except ImportError:
            raise MigrationBatchError('Install schemawright[mysql]') from None
        connect = pymysql.connect
    conn = None
    locked = False
    ddl_attempted = False
    completed: list[str] = []
    lock_name = 'schemawright:migrate:' + hashlib.sha256(schema.encode()).hexdigest()[:32]
    try:
        conn = connect(host=host, port=port, user=username, password=password,
                       database=schema, connect_timeout=5, read_timeout=5,
                       write_timeout=5, autocommit=True, charset='utf8mb4')
        with conn.cursor() as cur:
            # Lock applies even to preflight, so preview and apply inspect a stable
            # state with respect to other SchemaWright writers.
            cur.execute('SELECT GET_LOCK(%s,%s)', (lock_name, 5))
            if cur.fetchone() != (1,):
                raise MigrationBatchError('Cannot acquire SchemaWright migration lock')
            locked = True
            cur.execute('SELECT TABLE_NAME,TABLE_TYPE FROM information_schema.TABLES '
                        'WHERE TABLE_SCHEMA=%s', (schema,))
            present_rows = cur.fetchall()
            present = {}
            for name, kind in present_rows:
                lname = str(name).lower()
                if lname in present:
                    raise MigrationBatchError('Ambiguous schema object names')
                present[lname] = kind
            if present.get('schema_migrations') != 'BASE TABLE':
                raise MigrationBatchError('Migration history table is missing or invalid')
            cur.execute('SELECT type,version,install_order,filename,checksum,success '
                        'FROM `schema_migrations`')
            history = {}
            for kind, version, order, filename, digest, success in cur.fetchall():
                key = (str(kind).upper(), int(version), int(order))
                if key in history:
                    raise MigrationBatchError('Duplicate history identity')
                history[key] = (filename, digest, success)
            local = {f[0]: f for f in files}
            for key, (name, digest, success) in history.items():
                f = local.get(key)
                if f is None or success != 1 or name != f[1].name or digest != f[3]:
                    raise MigrationBatchError('Unknown, failed, or drifted migration history')
            pending = []
            found_pending = False
            for f in files:
                key, p, sql, digest, table = f
                if key in history:
                    if found_pending:
                        raise MigrationBatchError('History ordering gap: applied migration follows a pending one')
                    if present.get(table.lower()) != 'BASE TABLE':
                        raise MigrationBatchError('Recorded migration target table is missing')
                else:
                    found_pending = True
                    if table.lower() in present:
                        raise MigrationBatchError('Unrecorded target table exists; recovery inspection required')
                    pending.append(f)
            selected = pending[:max_migrations]
            base = {'database': db.name, 'target': schema,
                    'pendingBefore': len(pending), 'selected': [f[1].name for f in selected],
                    'maxMigrations': max_migrations}
            if dry_run or not selected:
                return {**base, 'action': 'would-apply' if selected else 'up-to-date',
                        'completed': [], 'changesExecuted': False}
            for key, p, sql, digest, table in selected:
                if p.is_symlink() or checksum(p) != digest or p.read_text(encoding='utf-8') != sql:
                    raise MigrationBatchError('Migration file changed before execution', completed)
                # Recheck object absence immediately before execution.
                cur.execute('SELECT COUNT(*) FROM information_schema.TABLES '
                            'WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s', (schema, table))
                if cur.fetchone() != (0,):
                    raise MigrationBatchError('Table appeared during batch; manual inspection required', completed)
                ddl_attempted = True
                start = time.monotonic()
                cur.execute(sql)
                elapsed = int((time.monotonic() - start) * 1000)
                # DDL committed. If the history insert fails, do NOT retry.
                cur.execute('INSERT INTO `schema_migrations` '
                            '(type,version,install_order,description,filename,checksum,execution_ms,success) '
                            'VALUES (%s,%s,%s,%s,%s,%s,%s,1)',
                            ('V', key[1], key[2], p.name.rsplit('__', 1)[-1][:-4],
                             p.name, digest, elapsed))
                completed.append(p.name)
                ddl_attempted = False
            return {**base, 'action': 'applied', 'completed': completed,
                    'remaining': len(pending) - len(selected), 'changesExecuted': True}
    except MigrationBatchError as exc:
        if completed and not exc.completed:
            raise MigrationBatchError(str(exc), completed) from None
        raise
    except Exception:
        detail = ('DDL or history write may have committed; manual recovery inspection required'
                  if ddl_attempted else 'Batch preflight or execution failed; inspect state before retrying')
        raise MigrationBatchError(detail, completed) from None
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
