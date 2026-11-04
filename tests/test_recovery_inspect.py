import json
from pathlib import Path
import pytest
from schemawright.configwright_import import import_configwright
from schemawright.recovery_inspect import inspect_recovery, RecoveryInspectError
from schemawright.files import checksum


def make(tmp_path):
    p=tmp_path/'databases.json'
    p.write_text(json.dumps({'appdb': {'driver':'mysql','host':'127.0.0.1','port':3306,'database':'schemawright_test'}}))
    db=import_configwright(p,root=tmp_path,database='appdb').databases['appdb']
    db.migrations.active.mkdir(parents=True)
    f=db.migrations.active/'V0001__0010__create_probe.sql'
    f.write_text('CREATE TABLE schemawright_probe (id BIGINT PRIMARY KEY) ENGINE=InnoDB;\n')
    return db, f


class Cursor:
    def __init__(self, c): self.c=c
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def execute(self, query, params=()):
        assert query.startswith('SELECT '), query
        self.c.queries.append(query)
        self.last=query
    def fetchall(self):
        if 'information_schema.TABLES' in self.last: return [(name,) for name in self.c.tables]
        return self.c.history


class Connection:
    def __init__(self, tables=('schema_migrations',), history=()):
        self.tables=set(tables);self.history=list(history);self.queries=[];self.closed=False;self.rolled=False
    def cursor(self): return Cursor(self)
    def rollback(self): self.rolled=True
    def close(self): self.closed=True


def inspect(db, c):
    result=inspect_recovery(db,username='schemawright_app',password='private',connect=lambda **kw:c)
    assert c.closed and c.rolled
    assert result['readOnly'] and result['changesExecuted'] is False
    assert all(x.startswith('SELECT ') for x in c.queries)
    return result


def record(f, digest=None):
    return ('V',1,10,f.name,digest or checksum(f),1)


def test_normal_pending(tmp_path):
    db,f=make(tmp_path)
    res=inspect(db,Connection())
    assert res['items'][0]['state']=='pending' and res['safeToProceed']


def test_interrupted_ddl_detected_without_reconciliation(tmp_path):
    db,f=make(tmp_path)
    res=inspect(db,Connection(tables=('schema_migrations','schemawright_probe')))
    assert res['items'][0]['state']=='unrecorded_table'
    assert not res['safeToProceed'] and not res['reconciliationPerformed']


def test_applied_then_missing_table(tmp_path):
    db,f=make(tmp_path)
    assert inspect(db,Connection(tables=('schema_migrations','schemawright_probe'),history=[record(f)]))['items'][0]['state']=='applied'
    result=inspect(db,Connection(history=[record(f)]))
    assert result['items'][0]['state']=='missing_target_table' and not result['safeToProceed']


def test_checksum_drift(tmp_path):
    db,f=make(tmp_path); old=record(f)
    f.write_text('CREATE TABLE schemawright_probe (id INT PRIMARY KEY);')
    result=inspect(db,Connection(tables=('schema_migrations','schemawright_probe'),history=[old]))
    assert result['items'][0]['state']=='checksum_drift' and not result['safeToProceed']


def test_filename_drift_and_failed_history(tmp_path):
    db,f=make(tmp_path)
    res=inspect(db,Connection(history=[('V',1,10,'old.sql',checksum(f),1)]))
    assert res['items'][0]['state']=='filename_drift'
    res=inspect(db,Connection(history=[('V',1,10,f.name,checksum(f),0)]))
    assert res['items'][0]['state']=='failed_history'


def test_history_missing(tmp_path):
    db,f=make(tmp_path)
    result=inspect(db,Connection(tables=()))
    assert not result['historyTableExists'] and not result['safeToProceed']


def test_unknown_record(tmp_path):
    db,f=make(tmp_path)
    result=inspect(db,Connection(history=[('V',3,10,'missing.sql','abc',1)]))
    assert any(x['state']=='missing_local' for x in result['items'])
    assert not result['safeToProceed']


def test_driver_error_redacted(tmp_path):
    db,f=make(tmp_path)
    def failing(**kw): raise RuntimeError('password: private')
    with pytest.raises(RecoveryInspectError) as e:
        inspect_recovery(db,username='schemawright_app',password='private',connect=failing)
    assert 'private' not in str(e.value)


def test_requires_valid_target(tmp_path):
    db,f=make(tmp_path);db.settings['host']='remote.invalid'
    with pytest.raises(RecoveryInspectError):
        inspect_recovery(db,username='a',password='b')
