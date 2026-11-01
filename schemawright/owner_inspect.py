"""Owner-authorized MySQL/MariaDB discovery. No provisioning or migrations.

No password in argv, persistence, or diagnostic output. Connection exceptions are
intentionally replaced with constant messages, without chaining.
"""
from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
import ssl
from typing import Callable, Any

from .config import DatabaseConfig


class OwnerInspectError(ValueError):
    """Safe error that excludes driver exception details and supplied credentials."""


@dataclass(frozen=True)
class OwnerInspection:
    database_id: str
    engine_version: str
    database_exists: bool
    grants_visible: bool
    encrypted_transport: bool
    inspected_only: bool = True


def _local_host(host: str) -> bool:
    if host.lower() in {'localhost', 'localhost.', '127.0.0.1', '::1'}:
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def inspect_owner_database(db: DatabaseConfig, *, username: str, password: str,
                           connect: Callable[..., Any] | None = None) -> OwnerInspection:
    """Inspect a server without SQL writes. Only the selected database is inspected.

    `connect` is injectable for tests. External connections require verified TLS,
    not merely an SSL option sent to the database driver.
    """
    if db.driver.lower() != 'mysql':
        raise OwnerInspectError('Owner inspection currently supports MySQL/MariaDB only')
    if not username or not username.strip() or not password:
        raise OwnerInspectError('A username and nonempty password are required')
    settings = db.settings
    host = settings.get('host', 'localhost')
    port = settings.get('port', 3306)
    database = settings.get('database')
    if not isinstance(host, str) or not host.strip() or not isinstance(database, str) or not database.strip():
        raise OwnerInspectError('Invalid database connection settings')
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise OwnerInspectError('Invalid database port')
    if connect is None:
        try:
            import pymysql
        except ImportError:
            raise OwnerInspectError('MySQL connector unavailable; install schemawright[mysql]') from None
        connect = pymysql.connect
    connection = None
    try:
        # Never select the target database at connection time; it may not exist yet.
        # Explicit short timeout avoids hanging during interactive installation.
        kwargs = dict(host=host, port=port, user=username, password=password,
                      connect_timeout=5, read_timeout=5, write_timeout=5,
                      autocommit=False, charset='utf8mb4')
        if not _local_host(host):
            # PyMySQL enables TLS using its ssl argument. Verification of the
            # negotiated cipher is additionally performed below.
            kwargs['ssl'] = ssl.create_default_context()
        connection = connect(**kwargs)
        if not _local_host(host):
            with connection.cursor() as cursor:
                cursor.execute('SHOW STATUS LIKE %s', ('Ssl_cipher',))
                row = cursor.fetchone()
                if not row or len(row) < 2 or not row[1]:
                    raise OwnerInspectError('Remote database connection did not negotiate TLS')
        with connection.cursor() as cursor:
            cursor.execute('SELECT VERSION()')
            version = cursor.fetchone()
            cursor.execute('SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s', (database,))
            count = cursor.fetchone()
            # SHOW GRANTS reveals the current principal's grants; never print them.
            grants_visible = False
            try:
                cursor.execute('SHOW GRANTS FOR CURRENT_USER()')
                grants_visible = bool(cursor.fetchall())
            except Exception:
                # Not every engine/user can show grants; discovery still succeeds.
                pass
        if not version or not isinstance(version[0], str) or not count:
            raise OwnerInspectError('Database inspection returned incomplete results')
        return OwnerInspection(db.name, version[0], bool(count[0]), grants_visible,
                               not _local_host(host))
    except OwnerInspectError:
        raise
    except Exception:
        raise OwnerInspectError('Database inspection failed; verify server connectivity and credentials') from None
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
