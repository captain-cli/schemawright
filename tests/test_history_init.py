import json
import pytest
from schemawright.configwright_import import import_configwright
from schemawright.history_init import initialize_history, HistoryInitError


def db(tmp_path, *, schema='schemawright_test'):
    src=tmp_path/'db.json'
    src.write_text(json.dumps({'appdb': {'driver':'mysql', 'host':'127.0.0.1',
                                         'port':3306, 'database':schema}}))
    return import_configwright(src, root=tmp_path, database='appdb').databases['appdb']


class Cursor:
    def __init__(self, conn): self.conn=conn
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def execute(self, sql, params=()):
        self.conn.sql.append(sql)
        if sql.startswith('CREATE TABLE'):
            self.conn.created=True
            if self.conn.fail_create: raise RuntimeError('secret password in driver exception')
        self.last=sql
    def fetchone(self):
        if 'GET_LOCK' in self.last: return (1,)
        if 'COUNT(*)' in self.last: return (0,)
        return None
    def fetchall(self): return [(x,) for x in self.conn.tables]


class Conn:
    def __init__(self, tables=(), fail_create=False):
        self.tables=tables;self.sql=[];self.closed=False;self.created=False;self.fail_create=fail_create
    def cursor(self): return Cursor(self)
    def close(self): self.closed=True


def call(db, conn, **kw):
    return initialize_history(db, username='schemawright_migrator',password='private',
                              connect=lambda **opts: conn, **kw)


def test_dry_run_never_writes(tmp_path):
    conn=Conn()
    result=call(db(tmp_path),conn)
    assert result.action=='would-create-history' and not result.changes_executed
    assert not conn.created and conn.closed
    assert all(s.startswith('SELECT ') for s in conn.sql)


def test_approved_creates_only_history(tmp_path):
    conn=Conn()
    result=call(db(tmp_path),conn,approved=True,dry_run=False)
    assert result.action=='created-history' and result.history_exists
    assert sum(s.startswith('CREATE TABLE') for s in conn.sql)==1
    assert 'schema_migrations' in next(s for s in conn.sql if s.startswith('CREATE TABLE'))
    assert conn.closed


@pytest.mark.parametrize('tables', [('existing',),('schema_migrations',),('existing','schema_migrations')])
def test_reject_nonempty_schema(tmp_path,tables):
    conn=Conn(tables)
    with pytest.raises(HistoryInitError): call(db(tmp_path),conn,approved=True,dry_run=False)
    assert not conn.created and conn.closed


def test_requires_approval(tmp_path):
    with pytest.raises(HistoryInitError): call(db(tmp_path),Conn(),dry_run=False)


def test_reject_app_account(tmp_path):
    with pytest.raises(HistoryInitError):
        initialize_history(db(tmp_path),username='schemawright_app',password='x',connect=lambda **kw:Conn())


def test_reject_other_engine_or_remote_host(tmp_path):
    d=db(tmp_path);d.settings['host']='remote.example.com'
    with pytest.raises(HistoryInitError): call(d,Conn())
    d.settings['host']='127.0.0.1';d.driver='postgres'
    with pytest.raises(HistoryInitError): call(d,Conn())


def test_failed_create_is_redacted(tmp_path):
    conn=Conn(fail_create=True)
    with pytest.raises(HistoryInitError) as err: call(db(tmp_path),conn,dry_run=False,approved=True)
    assert 'password' not in str(err.value) and conn.closed
