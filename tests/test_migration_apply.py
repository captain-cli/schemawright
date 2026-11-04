import json
from pathlib import Path
import pytest

from schemawright.configwright_import import import_configwright
from schemawright.migration_apply import migrate_one, MigrationApplyError


def project(tmp_path):
    config = tmp_path / 'databases.json'
    config.write_text(json.dumps({'appdb': {'database': 'schemawright_test', 'driver': 'mysql',
                                            'host': '127.0.0.1', 'port': 3306}}))
    db = import_configwright(config, root=tmp_path, database='appdb').databases['appdb']
    db.migrations.active.mkdir(parents=True)
    p = db.migrations.active / 'V0001__0010__create_demo.sql'
    p.write_text('CREATE TABLE schemawright_demo (id BIGINT PRIMARY KEY);\n')
    return db, p


class Cursor:
    def __init__(self, db):
        self.db = db
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def execute(self, sql, params=()):
        self.db.queries.append((sql, params))
        self.sql, self.params = sql, params
        if sql.startswith('CREATE TABLE'):
            self.db.tables.add('schemawright_demo')
            if self.db.fail_create: raise RuntimeError('private secret')
        if sql.startswith('INSERT INTO'):
            if self.db.fail_insert: raise RuntimeError('private secret')
            self.db.history.append(('V', params[1], params[2], params[4], params[5], 1))
    def fetchone(self):
        if 'GET_LOCK' in self.sql: return (1,)
        if 'information_schema.TABLES' in self.sql:
            name = self.params[1]
            return (int(name == 'schema_migrations' or name in self.db.tables),)
        raise AssertionError(self.sql)
    def fetchall(self):
        if 'FROM `schema_migrations`' in self.sql: return list(self.db.history)
        raise AssertionError(self.sql)


class FakeConnection:
    def __init__(self, history=(), tables=(), fail_create=False, fail_insert=False):
        self.history = list(history)
        self.tables = set(tables)
        self.fail_create = fail_create
        self.fail_insert = fail_insert
        self.queries = []
        self.closed = False
    def cursor(self): return Cursor(self)
    def close(self): self.closed = True


def test_dry_run_no_writes(tmp_path):
    db, p = project(tmp_path)
    c = FakeConnection()
    result = migrate_one(db, username='schemawright_migrator', password='secret',
                         connect=lambda **kw: c)
    assert result['action'] == 'would-apply' and result['pending'] == 1
    assert result['migration'] == p.name and not result['changesExecuted']
    assert not any(q.startswith(('INSERT ', 'CREATE ', 'UPDATE ', 'DELETE ')) for q, _ in c.queries)
    assert c.closed


def test_approved_apply_records_checksum_and_up_to_date(tmp_path):
    db, p = project(tmp_path)
    c = FakeConnection()
    result = migrate_one(db, username='schemawright_migrator', password='secret',
                         approved=True, dry_run=False, connect=lambda **kw: c)
    assert result['action'] == 'applied' and result['changesExecuted']
    assert len(c.history) == 1 and len(c.history[0][4]) == 64
    assert sum(q.startswith('CREATE TABLE') for q, _ in c.queries) == 1
    assert sum(q.startswith('INSERT INTO') for q, _ in c.queries) == 1
    assert any('RELEASE_LOCK' in q for q, _ in c.queries) and c.closed
    c2 = FakeConnection(history=c.history, tables=c.tables)
    result = migrate_one(db, username='schemawright_migrator', password='secret', connect=lambda **kw: c2)
    assert result['action'] == 'up-to-date'


def test_refuse_failed_history_and_checksum_drift(tmp_path):
    db, p = project(tmp_path)
    for history in [('V',1,10,p.name,'f'*64,1), ('V',1,10,p.name,'f'*64,0)]:
        c = FakeConnection(history=[history])
        with pytest.raises(MigrationApplyError):
            migrate_one(db, username='schemawright_migrator', password='secret', connect=lambda **kw: c)
        assert all(not q.startswith('CREATE ') for q, _ in c.queries)


def test_refuse_existing_target_table_after_interrupted_apply(tmp_path):
    db, p = project(tmp_path)
    c = FakeConnection(tables={'schemawright_demo'})
    with pytest.raises(MigrationApplyError, match='already exists'):
        migrate_one(db, username='schemawright_migrator', password='secret',
                    approved=True, dry_run=False, connect=lambda **kw: c)
    assert not any(q.startswith(('CREATE ', 'INSERT ')) for q, _ in c.queries)


def test_failure_between_ddl_and_record_is_fail_closed(tmp_path):
    db, _ = project(tmp_path)
    c = FakeConnection(fail_insert=True)
    with pytest.raises(MigrationApplyError, match='manual') as err:
        migrate_one(db, username='schemawright_migrator', password='secret',
                    approved=True, dry_run=False, connect=lambda **kw: c)
    assert 'secret' not in str(err.value)
    assert 'schemawright_demo' in c.tables and c.history == []


def test_refuse_non_create_sql_and_multiple_statements(tmp_path):
    db, p = project(tmp_path)
    for contents in ['DROP TABLE foo;', 'CREATE TABLE x (id INT); DROP TABLE foo;',
                     'CREATE TABLE x AS SELECT * FROM foo;',
                     'CREATE TABLE x (id INT) -- comment']:
        p.write_text(contents)
        c = FakeConnection()
        with pytest.raises(MigrationApplyError):
            migrate_one(db, username='schemawright_migrator', password='secret', connect=lambda **kw: c)
        assert c.queries == []


def test_refuse_root_and_application_user(tmp_path):
    db, _ = project(tmp_path)
    for name in ('root', 'schemawright_app'):
        with pytest.raises(MigrationApplyError):
            migrate_one(db, username=name, password='secret', connect=lambda **kw: FakeConnection())


def test_refuse_unapproved_execution(tmp_path):
    db, _ = project(tmp_path)
    with pytest.raises(MigrationApplyError):
        migrate_one(db, username='schemawright_migrator', password='secret',
                    dry_run=False, connect=lambda **kw: FakeConnection())


def test_missing_history_blocks(tmp_path):
    db, _ = project(tmp_path)
    c = FakeConnection()
    original = c.cursor
    def bad_cursor():
        cur = original()
        fetchone = cur.fetchone
        def missing():
            if 'schema_migrations' in str(cur.params): return (0,)
            return fetchone()
        cur.fetchone = missing
        return cur
    c.cursor = bad_cursor
    with pytest.raises(MigrationApplyError, match='history'):
        migrate_one(db, username='schemawright_migrator', password='secret', connect=lambda **kw: c)
