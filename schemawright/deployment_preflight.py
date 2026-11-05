"""Phase 7A: read-only Captain database deployment readiness report.

This command does NOT apply migrations, initialize history, provision accounts,
modify credentials or infer that every possible schema is safe for deployment.
It reuses ConfigWright import, CredentialWright-managed env-file resolution,
and the existing SchemaWright read-only history/recovery inspectors.
"""
from __future__ import annotations

import stat
from pathlib import Path
from typing import Any, Callable

from .configwright_import import import_configwright
from .configwright_credentials import resolve_database_credentials
from .configwright_plan import plan_database
from .configwright_live_status import migration_status
from .recovery_inspect import inspect_recovery


class DeploymentPreflightError(ValueError):
    """Credential-safe preflight error, suitable for machine-readable output."""
    def __init__(self, code: str, stage: str):
        super().__init__(code)
        self.code = code
        self.stage = stage


def _private_file(path: str | Path) -> None:
    """Require a regular, non-symlinked owner-only secret file, without reading it."""
    try:
        p = Path(path)
        if p.is_symlink():
            raise ValueError('symlink')
        info = p.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
            raise ValueError('not a bounded regular file')
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError('other users can read secrets')
    except (OSError, ValueError):
        raise DeploymentPreflightError('secret_file_unsafe', 'credentials') from None


def deployment_preflight(*, source: str | Path, project_root: str | Path,
                         database: str, read_only_user: str,
                         secrets_file: str | Path,
                         connect: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Return a JSON-safe readiness snapshot for one local MySQL database.

    "ready" means current restricted-schema migration checks permit an owner-
    approved next action. It is NOT an authorization or production certification.
    Pending migrations are allowed; the report distinguishes ready from current.
    """
    if (not isinstance(database, str) or not database or not isinstance(read_only_user, str)
            or not read_only_user or read_only_user.lower() in ('root', 'schemawright_migrator')):
        raise DeploymentPreflightError('invalid_inspection_identity', 'configuration')

    try:
        config = import_configwright(source, root=project_root, database=database)
        db = config.databases[database]
        if db.driver.lower() != 'mysql':
            raise DeploymentPreflightError('unsupported_driver', 'configuration')
        entries, plan_issues = plan_database(db)
        if plan_issues:
            return _blocked(db.name, db.settings.get('database'), 'migration_plan_invalid',
                            'migration_plan', count=len(plan_issues))
    except DeploymentPreflightError:
        raise
    except (ValueError, OSError, UnicodeError, KeyError, TypeError):
        raise DeploymentPreflightError('invalid_configuration', 'configuration') from None

    # ConfigWright's passwordRef names an APPLICATION secret. Never use the
    # migrator identity or probe a separate administrator credential here.
    if not isinstance(db.settings.get('passwordRef'), str):
        raise DeploymentPreflightError('password_reference_missing', 'credentials')
    _private_file(secrets_file)
    try:
        resolved = resolve_database_credentials(db, secrets_file)
        password = resolved.get('passwordRef')
        if not isinstance(password, str) or not password:
            raise ValueError('no password')
    except Exception:
        # Never print exception text or secret references from the resolver.
        raise DeploymentPreflightError('credential_unavailable', 'credentials') from None

    try:
        live = migration_status(db, username=read_only_user, password=password, connect=connect)
        recovery = inspect_recovery(db, username=read_only_user, password=password, connect=connect)
    except Exception:
        raise DeploymentPreflightError('database_inspection_failed', 'database_inspection') from None
    finally:
        password = None  # Keep resolved secrets out of the report; avoid logging them.
        resolved = None

    # Neither inspector is a full SQL execution authorization. Deny readiness
    # if they disagree, report a blocking state, or discover unsupported SQL.
    counts = recovery.get('counts', {})
    states = {str(k): int(v) for k, v in counts.items()}
    blocked = (not live.get('safeToPlan') or not recovery.get('safeToProceed')
               or not live.get('historyTableExists') or not recovery.get('historyTableExists')
               or bool(live.get('issues')) or bool(recovery.get('issues'))
               or any(k not in ('pending', 'applied') for k in states))
    if blocked:
        status, code = 'blocked', 'migration_state_blocked'
    else:
        status, code = 'ready', None
    return {
        'database': db.name,
        'target': db.settings.get('database'),
        'status': status,
        'readyToDeploy': not blocked,
        'upToDate': not blocked and states.get('pending', 0) == 0,
        'readOnly': True,
        'changesExecuted': False,
        'checks': {
            'configwright': {'status': 'passed'},
            'credentialwright': {'status': 'passed', 'credentialValuesExposed': False},
            'migrationPlan': {'status': 'passed', 'activeSqlFiles': sum(1 for x in entries if x['stage'] == 'active')},
            'migrationHistory': {'status': 'blocked' if not live.get('safeToPlan') else 'passed'},
            'recoveryInspection': {'status': 'blocked' if not recovery.get('safeToProceed') else 'passed'},
        },
        'migrationCounts': states,
        'blockerCodes': [code] if code else [],
        'executionAuthorized': False,
        'scope': 'local-mysql-create-table-v1',
    }


def _blocked(database: str, target: Any, code: str, stage: str, *, count: int) -> dict[str, Any]:
    return {'database': database, 'target': target, 'status': 'blocked',
            'readyToDeploy': False, 'upToDate': False, 'readOnly': True,
            'changesExecuted': False, 'executionAuthorized': False,
            'checks': {'migrationPlan': {'status': 'blocked', 'issueCount': count}},
            'migrationCounts': {}, 'blockerCodes': [code],
            'scope': 'local-mysql-create-table-v1'}


def error_report(error: DeploymentPreflightError) -> dict[str, Any]:
    return {'status': 'error', 'readyToDeploy': False, 'readOnly': True,
            'changesExecuted': False, 'executionAuthorized': False,
            'errorCode': error.code, 'stage': error.stage,
            'scope': 'local-mysql-create-table-v1'}
