from pathlib import Path
from types import SimpleNamespace
import pytest

from schemawright.owner_apply import apply_owner_account, OwnerApplyError


class Cursor:
    def __init__(self, *, exists=True, account=False, fail_on=None):
        self.exists, self.account, self.fail_on = exists, account, fail_on
        self.commands = []
        self.current = ""
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, sql, args=None):
        self.commands.append((sql, args))
        self.current = sql
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError('secret-value-must-never-appear')
    def fetchone(self):
        if 'SCHEMATA' in self.current: return (int(self.exists),)
        if 'mysql.user' in self.current: return (int(self.account),)
        raise AssertionError(self.current)
    def fetchall(self): return [('GRANT USAGE',)]


class Connection:
    def __init__(self, cursor): self.cur = cursor; self.closed = False
    def cursor(self): return self.cur
    def close(self): self.closed = True


def db(): return SimpleNamespace(name='appdb', driver='mysql', settings={'database':'schemawright_test', 'host':'127.0.0.1', 'port':3306})


def run(tmp_path, cursor=None, **overrides):
    cursor = cursor or Cursor()
    conn = Connection(cursor)
    opts = {'admin_user':'root','admin_password':'private-admin','application_user':'schemawright_app', 'secret_file':tmp_path/'secrets.env', 'approved':True, 'connect':lambda **kwargs:conn}
    opts.update(overrides)
    return cursor, conn, opts


def test_requires_approval(tmp_path):
    _, _, opts = run(tmp_path, approved=False)
    with pytest.raises(OwnerApplyError, match='approval'):
        apply_owner_account(db(), **opts)
    assert not opts['secret_file'].exists()


def test_creates_scoped_account(tmp_path):
    cursor, conn, opts = run(tmp_path)
    result = apply_owner_account(db(), **opts)
    assert result.account_created and result.grants_applied
    assert conn.closed
    assert opts['secret_file'].stat().st_mode & 0o777 == 0o600
    sqls = [s for s, _ in cursor.commands]
    assert any('GRANT SELECT, INSERT, UPDATE, DELETE ON `schemawright_test`.*' in s for s in sqls)
    assert all('DROP' not in s and 'CREATE DATABASE' not in s for s in sqls)
    assert not any('private-admin' in s for s in sqls)


def test_account_exists_fails_closed(tmp_path):
    cursor, _, opts = run(tmp_path, Cursor(account=True))
    with pytest.raises(OwnerApplyError, match='exists'):
        apply_owner_account(db(), **opts)
    assert not opts['secret_file'].exists()
    assert not any('CREATE USER' in s for s, _ in cursor.commands)


def test_database_absent_fails_closed(tmp_path):
    cursor, _, opts = run(tmp_path, Cursor(exists=False))
    with pytest.raises(OwnerApplyError, match='absent'):
        apply_owner_account(db(), **opts)
    assert not opts['secret_file'].exists()


def test_refuses_existing_secret_file(tmp_path):
    cursor, _, opts = run(tmp_path)
    opts['secret_file'].write_text('existing')
    with pytest.raises(OwnerApplyError):
        apply_owner_account(db(), **opts)
    assert opts['secret_file'].read_text() == 'existing'
    assert not any('CREATE USER' in s for s, _ in cursor.commands)


def test_partial_failure_keeps_recovery_file_and_redacts(tmp_path):
    cursor, conn, opts = run(tmp_path, Cursor(fail_on='GRANT SELECT'))
    with pytest.raises(OwnerApplyError, match='after account creation') as e:
        apply_owner_account(db(), **opts)
    assert 'secret-value-must-never-appear' not in str(e.value)
    assert opts['secret_file'].exists() and conn.closed


def test_wildcard_account_host_rejected(tmp_path):
    _, _, opts = run(tmp_path, account_host='%')
    with pytest.raises(OwnerApplyError, match='loopback'):
        apply_owner_account(db(), **opts)


def test_remote_engine_rejected(tmp_path):
    _, _, opts = run(tmp_path)
    d = db(); d.settings['host']='db.example.org'
    with pytest.raises(OwnerApplyError, match='loopback'):
        apply_owner_account(d, **opts)
