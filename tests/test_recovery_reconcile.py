import json
import pytest
from schemawright.configwright_import import import_configwright
from schemawright.recovery_reconcile import reconcile_one, ReconcileError
from schemawright.files import checksum


def sample(tmp_path):
    cfg = tmp_path / 'databases.json'
    cfg.write_text(json.dumps({'appdb': {'driver': 'mysql', 'host': '127.0.0.1',
                                        'port': 3306, 'database': 'schemawright_test'}}))
    db = import_configwright(cfg, root=tmp_path, database='appdb').databases['appdb']
    db.migrations.active.mkdir(parents=True)
    f = db.migrations.active / 'V0002__0010__create_probe.sql'
    f.write_text('CREATE TABLE schemawright_probe (id BIGINT NOT NULL PRIMARY KEY) ENGINE=InnoDB;\n')
    return db, f


class Cursor:
    def __init__(self, c): self.c = c
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, sql, params=()):
        self.c.calls.append((sql, params))
        self.sql, self.params = sql, params
        if sql.startswith('INSERT INTO'):
            if self.c.fail_insert: raise RuntimeError('secret in driver error')
            self.c.records.append((params[0], params[1], params[2], params[4], params[5], 1))
    def fetchall(self):
        q = self.sql
        if 'FROM `schema_migrations`' in q: return list(self.c.records)
        if 'information_schema.TABLES ' in q: return [('BASE TABLE', 'InnoDB')]
        if 'information_schema.COLUMNS' in q: return self.c.columns
        if 'information_schema.STATISTICS' in q: return [('PRIMARY', 0, 'id', 1)]
        if 'information_schema.TABLE_CONSTRAINTS' in q: return [('PRIMARY KEY',)]
        raise AssertionError(q)
    def fetchone(self):
        if 'GET_LOCK' in self.sql: return (1,)
        if 'TRIGGERS' in self.sql: return (0,)
        if 'COUNT(*) FROM `schemawright_probe`' in self.sql: return (self.c.rows,)
        raise AssertionError(self.sql)


class Connection:
    def __init__(self, records=(), rows=0, columns=None, fail_insert=False):
        self.records=list(records); self.rows=rows; self.fail_insert=fail_insert
        self.columns = columns if columns is not None else [('id', 'bigint', 'NO', 'PRI', None, '')]
        self.calls=[]; self.closed=False
    def cursor(self): return Cursor(self)
    def close(self): self.closed=True


def run(db, f, conn, **kwargs):
    return reconcile_one(db, username='schemawright_migrator', password='private', filename=f.name,
                         connect=lambda **kw:conn, **kwargs)


def test_dry_run_never_writes(tmp_path):
    db,f=sample(tmp_path); c=Connection()
    r=run(db,f,c)
    assert r['structureVerified'] and r['action']=='would-record-history'
    assert not r['changesExecuted'] and c.closed
    assert not any(s.startswith(('CREATE ', 'INSERT ', 'DELETE ', 'UPDATE ')) for s,_ in c.calls)


def test_apply_only_inserts_history(tmp_path):
    db,f=sample(tmp_path); c=Connection()
    r=run(db,f,c,dry_run=False,approved=True)
    assert r['action']=='recorded-history' and r['changesExecuted']
    assert len(c.records)==1 and c.records[0][4]==checksum(f)
    assert len([q for q,_ in c.calls if q.startswith('INSERT INTO')])==1
    assert not any(q.startswith(('CREATE ', 'ALTER ', 'DROP ')) for q,_ in c.calls)
    assert any('RELEASE_LOCK' in q for q,_ in c.calls)


def test_refuse_existing_history(tmp_path):
    db,f=sample(tmp_path)
    c=Connection(records=[('V',2,10,f.name,checksum(f),1)])
    with pytest.raises(ReconcileError,match='already has history'):
        run(db,f,c,approved=True,dry_run=False)
    assert not any(q.startswith('INSERT ') for q,_ in c.calls)


def test_refuse_different_column_type(tmp_path):
    db,f=sample(tmp_path); c=Connection(columns=[('id','int','NO','PRI',None,'')])
    with pytest.raises(ReconcileError,match='differ'):
        run(db,f,c,approved=True,dry_run=False)
    assert not any(q.startswith('INSERT ') for q,_ in c.calls)


def test_refuse_nonempty_table(tmp_path):
    db,f=sample(tmp_path); c=Connection(rows=4)
    with pytest.raises(ReconcileError,match='contains data'):
        run(db,f,c,approved=True,dry_run=False)


def test_refuse_unsupported_sql(tmp_path):
    db,f=sample(tmp_path)
    f.write_text('CREATE TABLE schemawright_probe (id BIGINT PRIMARY KEY, name TEXT);')
    c=Connection()
    with pytest.raises(ReconcileError): run(db,f,c)
    assert not c.calls


def test_refuse_without_approval(tmp_path):
    db,f=sample(tmp_path)
    with pytest.raises(ReconcileError): run(db,f,Connection(),dry_run=False)


def test_insert_failure_redacted(tmp_path):
    db,f=sample(tmp_path); c=Connection(fail_insert=True)
    with pytest.raises(ReconcileError) as exc: run(db,f,c,approved=True,dry_run=False)
    assert 'secret' not in str(exc.value) and c.closed

def test_pymysql_fetchall_tuple_of_tuples(tmp_path, monkeypatch):
    """Real PyMySQL returns tuples, unlike the original list-based test fake."""
    db, f = sample(tmp_path)
    conn = Connection()
    original = Cursor.fetchall
    monkeypatch.setattr(Cursor, 'fetchall', lambda self: tuple(original(self)))
    result = run(db, f, conn)
    assert result['action'] == 'would-record-history'
    assert result['structureVerified'] is True
    assert not any(query.startswith('INSERT ') for query, _ in conn.calls)
