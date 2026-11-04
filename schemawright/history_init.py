"""Opt-in MySQL migration history initialization for a provably empty schema.

Never create a database, account, or application table. MySQL DDL is not
transactional; failures after CREATE TABLE require independent inspection.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Callable

from .config import DatabaseConfig
from .owner_inspect import _local_host

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")


class HistoryInitError(ValueError):
    """Deliberately excludes raw driver errors and credentials."""


@dataclass(frozen=True)
class HistoryInitResult:
    database: str
    target: str
    schema_empty: bool
    history_exists: bool
    action: str
    changes_executed: bool


def initialize_history(db: DatabaseConfig, *, username: str, password: str,
                       approved: bool = False, dry_run: bool = True,
                       connect: Callable[..., Any] | None = None) -> HistoryInitResult:
    """Preflight or create only the history table, under a DB advisory lock.

    A separate migration identity is required. The application account
    schemawright_app is expressly refused; caller is responsible for granting
    narrowly scoped CREATE/SELECT permissions to the migrator beforehand.
    """
    if db.driver.lower() != 'mysql':
        raise HistoryInitError('History initialization supports MySQL/MariaDB only')
    opts = db.settings
    host, port, schema = opts.get('host', 'localhost'), opts.get('port', 3306), opts.get('database')
    if not isinstance(host, str) or not _local_host(host):
        raise HistoryInitError('History initialization currently requires a loopback MySQL server')
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise HistoryInitError('Invalid MySQL port')
    if not isinstance(schema, str) or not _IDENT.fullmatch(schema):
        raise HistoryInitError('Invalid target schema identifier')
    if not isinstance(username, str) or not _IDENT.fullmatch(username) or username.lower() in {'schemawright_app', 'root'}:
        raise HistoryInitError('A dedicated migrator login is required')
    if not password:
        raise HistoryInitError('Migration account authentication is required')
    if not dry_run and not approved:
        raise HistoryInitError('Explicit initialization approval is required')
    if connect is None:
        try:
            import pymysql
        except ImportError:
            raise HistoryInitError('MySQL connector unavailable; install schemawright[mysql]') from None
        connect = pymysql.connect

    connection = None
    locked = False
    create_attempted = False
    # Advisory lock is scoped to current MySQL connection, not to local process.
    lock_name = 'schemawright:init:' + hashlib.sha256(schema.encode('utf-8')).hexdigest()[:32]
    try:
        connection = connect(host=host, port=port, user=username, password=password,
                             database=schema, connect_timeout=5, read_timeout=5,
                             write_timeout=5, autocommit=True, charset='utf8mb4')
        with connection.cursor() as cursor:
            cursor.execute('SELECT GET_LOCK(%s, %s)', (lock_name, 5))
            outcome = cursor.fetchone()
            if not outcome or outcome[0] != 1:
                raise HistoryInitError('Cannot acquire migration-history initialization lock')
            locked = True
            # Require actual target presence and inspect every table/view. No blind baseline.
            cursor.execute('SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s', (schema,))
            names = [str(row[0]) for row in cursor.fetchall()]
            if names:
                if 'schema_migrations' in names:
                    raise HistoryInitError('Migration history already exists; initialization refused')
                raise HistoryInitError('Target schema is not empty; initialization refused')
            if dry_run:
                return HistoryInitResult(db.name, schema, True, False, 'would-create-history', False)
            # We have the lock, but no guarantees against unrelated DDL actors.
            # Plain CREATE TABLE (no IF NOT EXISTS) will fail closed on a race.
            create_attempted = True
            cursor.execute('''CREATE TABLE `schema_migrations` (
                `type` VARCHAR(8) NOT NULL,
                `version` INTEGER NOT NULL,
                `install_order` INTEGER NOT NULL,
                `description` TEXT NOT NULL,
                `filename` VARCHAR(255) NOT NULL,
                `checksum` CHAR(64) NOT NULL,
                `applied_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                `execution_ms` INTEGER NULL,
                `success` TINYINT NOT NULL DEFAULT 1,
                PRIMARY KEY (`type`, `version`, `install_order`)
            ) ENGINE=InnoDB''')
            cursor.execute('SELECT COUNT(*) FROM `schema_migrations`')
            row = cursor.fetchone()
            if not row or row[0] != 0:
                raise HistoryInitError('History initialization verification failed; inspect manually')
            return HistoryInitResult(db.name, schema, True, True, 'created-history', True)
    except HistoryInitError:
        raise
    except Exception:
        if create_attempted:
            raise HistoryInitError('Initialization failed after CREATE attempt; inspect history table manually before retrying') from None
        raise HistoryInitError('History initialization preflight failed; verify target and migrator permissions') from None
    finally:
        if connection is not None:
            if locked:
                try:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT RELEASE_LOCK(%s)', (lock_name,))
                except Exception:
                    pass
            try:
                connection.close()
            except Exception:
                pass
