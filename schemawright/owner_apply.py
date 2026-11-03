"""Owner-approved MySQL application account provisioning.

This module never touches an existing account, never creates/alters a database,
and only grants DML on a single pre-existing named schema. Secrets are never
included in exceptions or results. Approval occurs in the CLI before invocation.
"""
from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Callable
import os
import re
import secrets
import ssl

from .config import DatabaseConfig


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"


class OwnerApplyError(ValueError):
    """Safe error without driver details or credentials."""


@dataclass(frozen=True)
class OwnerApplyResult:
    database_id: str
    account: str
    account_host: str
    account_created: bool
    grants_applied: bool
    secret_file: str


def _loopback(host: str) -> bool:
    if not isinstance(host, str):
        return False
    if host.lower() in {"localhost", "localhost."}:
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _name(value: str, kind: str) -> str:
    if not isinstance(value, str) or len(value) > 64 or not _IDENTIFIER.fullmatch(value):
        raise OwnerApplyError(f"Invalid {kind} identifier")
    return value


def _new_secret_file(path: str | Path, key: str, secret: str) -> None:
    """Exclusive, private creation before issuing CREATE USER.

    A pre-existing file is never overwritten. It is intentionally retained on
    a partial SQL failure so the operator can recover the generated secret.
    """
    output = Path(path)
    if output.is_symlink():
        raise OwnerApplyError("Secret destination must not be a symlink")
    fd = os.open(str(output), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(f"{key}={secret}\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            output.unlink()
        except OSError:
            pass
        raise


def apply_owner_account(db: DatabaseConfig, *, admin_user: str, admin_password: str,
                        application_user: str, secret_file: str | Path,
                        account_host: str = "127.0.0.1", approved: bool = False,
                        connect: Callable[..., Any] | None = None) -> OwnerApplyResult:
    """Apply an approved new-account-only plan to an *existing* schema.

    Requires direct owner approval via caller; never reuse or modify an account.
    Only localhost / loopback database hosts in this initial implementation.
    No database creation, schema migration, or account deletion.
    """
    if not approved:
        raise OwnerApplyError("Explicit owner approval required; no changes executed")
    if db.driver.lower() != "mysql":
        raise OwnerApplyError("Owner account provisioning currently supports MySQL only")
    account = _name(application_user, "application user")
    _name(admin_user, "administrator user")
    if account == admin_user:
        raise OwnerApplyError("Application user must differ from administrator")
    if account_host not in {"127.0.0.1", "localhost", "::1"}:
        raise OwnerApplyError("Only loopback account hosts are supported initially")
    if not admin_password:
        raise OwnerApplyError("Administrative authorization is required")
    settings = db.settings
    database = _name(settings.get("database"), "database")
    host = settings.get("host", "localhost")
    port = settings.get("port", 3306)
    if not _loopback(host):
        raise OwnerApplyError("Provisioning is restricted to loopback database servers in this phase")
    if isinstance(port, bool) or not isinstance(port, int) or not (1 <= port <= 65535):
        raise OwnerApplyError("Invalid database port")
    if connect is None:
        try:
            import pymysql
        except ImportError:
            raise OwnerApplyError("MySQL connector unavailable; install schemawright[mysql]") from None
        connect = pymysql.connect
    connection = None
    file_created = False
    user_created = False
    grants_applied = False
    try:
        connection = connect(host=host, port=port, user=admin_user, password=admin_password,
                             connect_timeout=5, read_timeout=5, write_timeout=5,
                             autocommit=True, charset="utf8mb4")
        with connection.cursor() as cursor:
            # Recheck both target and account immediately before any writes.
            cursor.execute("SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (database,))
            row = cursor.fetchone()
            if not row or int(row[0]) != 1:
                raise OwnerApplyError("Target database is absent; no changes executed")
            cursor.execute("SELECT COUNT(*) FROM mysql.user WHERE User=%s AND Host=%s", (account, account_host))
            row = cursor.fetchone()
            if not row or int(row[0]) != 0:
                raise OwnerApplyError("Application account exists or could not be verified; no changes executed")
            # Generate a long password and store it privately *before* MySQL DDL.
            # An owner-selected path is required; its directory must already exist.
            generated_password = secrets.token_urlsafe(48)
            _new_secret_file(secret_file, "APP_DB_PASSWORD", generated_password)
            file_created = True
            # Quote validated identifiers and account parts; bind password value.
            cursor.execute(f"CREATE USER '{account}'@'{account_host}' IDENTIFIED BY %s", (generated_password,))
            user_created = True
            cursor.execute(f"GRANT {_PRIVILEGES} ON `{database}`.* TO '{account}'@'{account_host}'")
            grants_applied = True
            cursor.execute("SHOW GRANTS FOR %s@%s", (account, account_host))
            if not cursor.fetchall():
                raise OwnerApplyError("Account created but grant verification failed")
        return OwnerApplyResult(db.name, account, account_host, user_created, grants_applied, str(secret_file))
    except OwnerApplyError:
        raise
    except Exception:
        if user_created:
            raise OwnerApplyError("Provisioning failed after account creation; manually inspect account and grants; private credential file retained") from None
        if file_created:
            raise OwnerApplyError("Provisioning failed before account creation; private credential file retained for review") from None
        raise OwnerApplyError("Provisioning aborted before changes; verify privileges and database state") from None
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
