# Schemawright

**Schemawright** is a project-agnostic SQL migration lifecycle tool for applications, monorepos, and deployment pipelines.

It standardizes migration naming, registration, migration-set workspaces, staging, promotion, rollback planning, and execution across SQL-backed projects.

## Naming standard

Schemawright uses three sortable parts:

```text
V0001__0010__create_users.sql
P0001__0020__refresh_user_summary.sql
T0001__0030__audit_user_changes.sql
S0001__0040__seed_roles.sql
R0001__0050__drop_users.sql
```

| Prefix | Meaning |
|---|---|
| `V` | Versioned schema/data migration |
| `P` | Stored procedure SQL |
| `T` | Trigger SQL |
| `S` | Seed SQL |
| `R` | Rollback/down SQL |

| Part | Meaning |
|---|---|
| `0001` | Schema/release version |
| `0010` | Install order |
| description | Human-readable slug |

This separates **version identity** from **install order**.

## Install

```bash
python -m pip install -e ".[dev]"
```

## Captain canonical project manifest

Schemawright also accepts a Captain project `manifest.json` (schema `captain/manifest/v1`).
Use `--canonical --root /path/to/project` to resolve its `schemawright` section
through Captain Core. The section may be inline or referenced from
`manifests.schemawright` using a `captain/tool-manifest/v1` envelope.
Relative SQL paths resolve against the Captain project root in this mode.

```bash
schemawright --canonical --root . --database app status
```

The legacy `--config schemawright.json` mode remains unchanged.

## Quick start

```bash
schemawright init
schemawright init-set --database app --name "user accounts" --next-version \
  --migration "create users" \
  --procedure "refresh user summary" \
  --trigger "audit user changes" \
  --seed "seed roles" \
  --rollback "drop users"

schemawright promote --database app --version 0001 --include-seeds --include-rollbacks
schemawright migrate --database app
schemawright status --database app
```

## Initialize a larger migration set

For large releases, registering files one at a time gets cumbersome. Use `init-set` to create a staged version workspace.

```bash
schemawright init-set \
  --database app \
  --name "user accounts" \
  --version 0003 \
  --migration "create users" \
  --migration "create user roles" \
  --procedure "refresh user summary" \
  --trigger "audit user changes" \
  --seed "seed roles" \
  --rollback "drop users"
```

Creates a workspace like:

```text
sql/migrations/staged/app/V0003__user_accounts/V0003__0010__create_users.sql
sql/migrations/staged/app/V0003__user_accounts/V0003__0020__create_user_roles.sql
sql/procedures/staged/app/V0003__user_accounts/P0003__0030__refresh_user_summary.sql
sql/triggers/staged/app/V0003__user_accounts/T0003__0040__audit_user_changes.sql
sql/seeds/staged/app/V0003__user_accounts/S0003__0050__seed_roles.sql
sql/rollbacks/staged/app/V0003__user_accounts/R0003__0060__drop_users.sql
```

You can also initialize an empty workspace with README files only:

```bash
schemawright init-set --database app --name "billing cleanup" --next-version
```

Schemawright recursively discovers SQL files under staged workspaces, then promotion copies them into the active flat directory.

## Register a single migration

Create an empty staged migration:

```bash
schemawright register \
  --database app \
  --type migration \
  --description "create users"
```

Create a migration with explicit version and order:

```bash
schemawright register \
  --database app \
  --type migration \
  --version 0002 \
  --order 0030 \
  --description "add indexes"
```

Register an existing SQL file:

```bash
schemawright register \
  --database app \
  --type migration \
  --version 0002 \
  --order 0030 \
  --description "add indexes" \
  --source ./scratch/add-indexes.sql
```

Supported types:

```text
migration, seed, rollback, procedure, trigger
```

## Promote staged files

By default, promotion includes migrations, stored procedures, and triggers:

```bash
schemawright promote --database app --version 0002 --dry-run
schemawright promote --database app --version 0002
```

Promote seeds and rollback files too:

```bash
schemawright promote --database app --version 0002 --include-seeds --include-rollbacks
```

Skip procedures or triggers when needed:

```bash
schemawright promote --database app --version 0002 --skip-procedures --skip-triggers
```

## Migrate and status

```bash
schemawright migrate --database app
schemawright migrate --database app --no-seeds
schemawright status --database app
```

Execution order is based on `(version, install_order)`, so procedures and triggers can be positioned exactly where they belong in the migration stream.

## Rollback planning

Rollback execution should be deliberate because destructive database operations need explicit SQL. Schemawright supports rollback file discovery and planning:

```bash
schemawright rollback --database app --to-version 0001
```

Rollback files use the `R` prefix:

```text
R0002__0030__remove_indexes.sql
```

## Config

```json
{
  "projectName": "example-app",
  "defaults": {
    "orderStep": 10,
    "stageByDefault": true
  },
  "databases": {
    "app": {
      "driver": "sqlite",
      "database": ".captain/runtime/db/app.sqlite",
      "migrations": {
        "staged": "sql/migrations/staged/app",
        "active": "sql/migrations/active/app",
        "archive": "sql/migrations/archive/app"
      },
      "procedures": {
        "staged": "sql/procedures/staged/app",
        "active": "sql/procedures/active/app",
        "archive": "sql/procedures/archive/app"
      },
      "triggers": {
        "staged": "sql/triggers/staged/app",
        "active": "sql/triggers/active/app",
        "archive": "sql/triggers/archive/app"
      },
      "seeds": {
        "staged": "sql/seeds/staged/app",
        "active": "sql/seeds/active/app",
        "archive": "sql/seeds/archive/app"
      },
      "rollbacks": {
        "staged": "sql/rollbacks/staged/app",
        "active": "sql/rollbacks/active/app",
        "archive": "sql/rollbacks/archive/app"
      }
    }
  }
}
```

## Commands

```text
schemawright init
schemawright init-set
schemawright register
schemawright validate
schemawright promote
schemawright status
schemawright migrate
schemawright repair
schemawright rebuild
schemawright rollback
```

## Supported database direction

SQLite is implemented first for local testing. The project includes driver lanes for MySQL/MariaDB, PostgreSQL/CockroachDB, Microsoft SQL Server, Oracle, and DuckDB so adapters can be filled in without changing the CLI contract.

## Captain integration

Schemawright ships with `captain/tool.json` and capabilities for database schema lifecycle commands.
