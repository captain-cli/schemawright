import json
import pytest
from schemawright.configwright_import import import_configwright
from schemawright.owner_inspect import inspect_owner_database, OwnerInspectError
from schemawright import cli


def db(tmp_path, *, host='localhost'):
    source = tmp_path / 'db.json'
    source.write_text(json.dumps({'appdb': {'driver': 'mysql', 'host': host, 'port': 3306,
                                            'database': 'existing_db', 'passwordRef': 'env:APP_DB_PASSWORD'}}))
    return source, import_configwright(source, root=tmp_path, database='appdb').databases['appdb']


class Cursor:
    def __init__(self, c): self.c=c
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def execute(self, query, params=()):
        self.c.queries.append((query, params)); self.query=query
    def fetchone(self):
        if self.query.startswith('SELECT VERSION'): return ('8.0.38',)
        if self.query.startswith('SHOW STATUS'): return ('Ssl_cipher', 'TLS_AES_256_GCM_SHA384')
        return (1,)
    def fetchall(self): return [('GRANT SELECT ON *.* TO user',)]

class Conn:
    def __init__(self): self.queries=[]; self.closed=False; self.rolled=False
    def cursor(self): return Cursor(self)
    def rollback(self): self.rolled=True
    def close(self): self.closed=True


def test_inspect_only_read_queries_and_closes(tmp_path):
    _, target=db(tmp_path)
    conn=Conn(); kwargs={}
    def connect(**kw): kwargs.update(kw); return conn
    result=inspect_owner_database(target,username='inspector',password='private',connect=connect)
    assert result.database_exists and result.grants_visible
    assert conn.closed and conn.rolled
    assert all(q.startswith(('SELECT ', 'SHOW ')) for q, _ in conn.queries)
    assert kwargs['password']=='private'
    assert 'database' not in kwargs


def test_connection_failure_redacts_secret(tmp_path):
    _, target=db(tmp_path)
    def bad(**kw): raise RuntimeError('password: private')
    with pytest.raises(OwnerInspectError) as exc:
        inspect_owner_database(target,username='inspector',password='private',connect=bad)
    assert 'private' not in str(exc.value)


def test_remote_uses_validating_tls(tmp_path):
    _, target=db(tmp_path,host='db.example.net')
    conn=Conn(); kwargs={}
    def connect(**kw): kwargs.update(kw); return conn
    result=inspect_owner_database(target,username='inspect',password='private',connect=connect)
    assert result.encrypted_transport
    assert kwargs['ssl'].verify_mode.name == 'CERT_REQUIRED'
    assert kwargs['ssl'].check_hostname


def test_reject_missing_password_before_connect(tmp_path):
    _, target=db(tmp_path)
    with pytest.raises(OwnerInspectError):
        inspect_owner_database(target, username='inspect', password='',connect=lambda **kw: None)


def test_cli_prompts_without_leaking(tmp_path,monkeypatch,capsys):
    source,_=db(tmp_path)
    monkeypatch.setattr(cli.getpass,'getpass',lambda prompt: 'super-secret')
    class Result:
        database_id='appdb'; engine_version='8.0'; database_exists=True
        grants_visible=False; encrypted_transport=False
    def inspect(target,*,username,password):
        assert password=='super-secret'; assert username=='inspector';return Result()
    monkeypatch.setattr(cli,'inspect_owner_database', inspect)
    rc=cli.main(['configwright-owner-inspect','--source',str(source), '--project-root',str(tmp_path),
                 '--database','appdb','--login-user','inspector'])
    out=capsys.readouterr()
    assert rc==0 and 'super-secret' not in out.out and 'super-secret' not in out.err
    assert 'changesExecuted' in out.out


def test_cli_failure_redacts(tmp_path,monkeypatch,capsys):
    source,_=db(tmp_path)
    monkeypatch.setattr(cli.getpass,'getpass',lambda prompt: 'super-secret')
    def fail(*args,**kwargs):raise OwnerInspectError('private detail')
    monkeypatch.setattr(cli,'inspect_owner_database',fail)
    rc=cli.main(['configwright-owner-inspect','--source',str(source), '--project-root',str(tmp_path),
                 '--database','appdb','--login-user','inspector'])
    captured=capsys.readouterr()
    assert rc==1 and 'private detail' not in captured.err and 'super-secret' not in captured.err
