from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from .config import DatabaseConfig
from .drivers import create_driver
from .files import list_active, checksum
from .model import MigrationFile

@dataclass
class RunOptions:
    root: Path
    dry_run: bool = False
    include_seeds: bool = True

class MigrationRunner:
    def __init__(self, db: DatabaseConfig, options: RunOptions):
        self.db = db
        self.options = options
        self.driver = create_driver(db.settings, options.root)

    def ensure_table(self) -> None:
        self.driver.execute_script(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              type TEXT NOT NULL,
              version INTEGER NOT NULL,
              install_order INTEGER NOT NULL,
              description TEXT NOT NULL,
              filename TEXT NOT NULL,
              checksum TEXT NOT NULL,
              applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
              execution_ms INTEGER,
              success INTEGER DEFAULT 1,
              PRIMARY KEY (type, version, install_order)
            );
            """
        )

    def applied_keys(self) -> set[tuple[str, int, int]]:
        rows = self.driver.query_all("SELECT type, version, install_order FROM schema_migrations WHERE success=1")
        return {(str(t), int(v), int(o)) for t, v, o in rows}

    def record(self, mf: MigrationFile, file_checksum: str, execution_ms: int) -> None:
        self.driver.execute(
            "INSERT INTO schema_migrations(type, version, install_order, description, filename, checksum, execution_ms, success) VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (mf.type, mf.version, mf.install_order, mf.description, mf.filename, file_checksum, execution_ms),
        )

    def plan(self) -> list[MigrationFile]:
        migrations = list_active(self.db, "V")
        procedures = list_active(self.db, "P")
        triggers = list_active(self.db, "T")
        seeds = list_active(self.db, "S") if self.options.include_seeds else []
        return sorted(migrations + procedures + triggers + seeds, key=lambda m: (m.version, m.install_order, m.type, m.filename))

    def status(self) -> list[tuple[str, MigrationFile]]:
        self.driver.connect()
        try:
            self.ensure_table()
            applied = self.applied_keys()
            result = []
            for mf in self.plan():
                state = "applied" if (mf.type, mf.version, mf.install_order) in applied else "pending"
                result.append((state, mf))
            return result
        finally:
            self.driver.close()

    def migrate(self) -> None:
        planned = self.plan()
        if self.options.dry_run:
            for mf in planned:
                print(f"would apply {mf.filename}")
            return
        self.driver.connect()
        try:
            self.ensure_table()
            applied = self.applied_keys()
            for mf in planned:
                key = (mf.type, mf.version, mf.install_order)
                if key in applied:
                    print(f"Already applied: {mf.filename}")
                    continue
                print(f"Applying: {mf.filename}")
                sql = mf.path.read_text(encoding="utf-8")
                started = time.perf_counter()
                self.driver.execute_script(sql)
                elapsed = int((time.perf_counter() - started) * 1000)
                self.record(mf, checksum(mf.path), elapsed)
        finally:
            self.driver.close()

    def rebuild(self) -> None:
        if self.options.dry_run:
            print(f"would rebuild database {self.db.name}")
            return
        self.driver.rebuild()
        self.migrate()

    def repair(self) -> None:
        if self.options.dry_run:
            print(f"would repair database {self.db.name}")
            return
        self.driver.connect()
        try:
            self.driver.repair()
        finally:
            self.driver.close()
        self.migrate()
