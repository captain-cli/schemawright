"""Owner-controlled, offline database adoption/provisioning plan.

No database access, passwords, grants or filesystem changes. The resulting plan
is a review artifact, not authorization to execute SQL.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from .config import DatabaseConfig

IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
NETWORK_DRIVERS = {"mysql", "postgres", "postgresql", "sqlserver", "oracle"}


class OwnerProvisionError(ValueError):
    """Safe error messages, never incorporating untrusted configuration values."""


@dataclass(frozen=True)
class OwnerProvisionPlan:
    database_id: str
    driver: str
    target_exists: bool
    create_database: bool
    application_user: str
    create_application_user: bool
    grants: tuple[str, ...]
    admin_authentication: str = "interactive-temporary"
    applied: bool = False


def plan_owner_provision(db: DatabaseConfig, *, existing_database: bool,
                         application_user: str, existing_user: bool = False) -> OwnerProvisionPlan:
    """Build a non-executable plan. Do not persist or accept administrator credentials."""
    if db.driver.lower() not in NETWORK_DRIVERS:
        raise OwnerProvisionError("Owner provisioning requires a supported server database engine")
    if not IDENTIFIER.fullmatch(application_user) or len(application_user) > 64:
        raise OwnerProvisionError("Application account must be a valid short identifier")
    if not isinstance(db.settings.get("database"), str) or not db.settings["database"].strip():
        raise OwnerProvisionError("A named database target is required")
    if not existing_database and existing_user:
        raise OwnerProvisionError("Existing application account requires an existing database target")
    # DB-specific grant syntax is intentionally deferred to an approved, driver-specific executor.
    return OwnerProvisionPlan(
        database_id=db.name, driver=db.driver.lower(), target_exists=existing_database,
        create_database=not existing_database, application_user=application_user,
        create_application_user=not existing_user,
        grants=("application-dml",),
    )
