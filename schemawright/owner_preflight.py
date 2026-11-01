"""Read-only owner provisioning preflight.

This is not authorization and does not provision accounts or databases.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .config import DatabaseConfig
from .owner_inspect import inspect_owner_database, OwnerInspection
from .owner_provision import OwnerProvisionPlan, plan_owner_provision


class OwnerPreflightError(ValueError):
    """An intentionally redacted, safe preflight error."""


@dataclass(frozen=True)
class OwnerPreflight:
    database_id: str
    engine_version: str
    database_exists: bool
    database_action: str
    account_action: str
    application_user: str
    grants_requested: tuple[str, ...]
    requires_owner_approval: bool = True
    authorized: bool = False
    executed: bool = False


def preflight_owner_provision(db: DatabaseConfig, *, username: str, password: str,
                              existing_database: bool, application_user: str,
                              existing_user: bool = False,
                              connect: Callable[..., Any] | None = None) -> OwnerPreflight:
    """Check actual database existence against the owner's stated intent.

    Account existence and grant availability are deliberately *not* asserted:
    introspection of mysql.user needs privileges beyond an ordinary inspector.
    """
    try:
        plan: OwnerProvisionPlan = plan_owner_provision(
            db, existing_database=existing_database, application_user=application_user,
            existing_user=existing_user)
        inspection: OwnerInspection = inspect_owner_database(
            db, username=username, password=password, connect=connect)
        if existing_database and not inspection.database_exists:
            raise OwnerPreflightError('Requested existing database does not exist; no action performed')
        if not existing_database and inspection.database_exists:
            raise OwnerPreflightError('Requested new database already exists; no action performed')
        return OwnerPreflight(
            database_id=plan.database_id,
            engine_version=inspection.engine_version,
            database_exists=inspection.database_exists,
            database_action='preserve-existing' if existing_database else 'create-after-approval',
            account_action='reuse-after-verification' if existing_user else 'create-after-approval',
            application_user=plan.application_user,
            grants_requested=plan.grants,
        )
    except OwnerPreflightError:
        raise
    except Exception:
        raise OwnerPreflightError('Owner provisioning preflight failed; no action performed') from None
