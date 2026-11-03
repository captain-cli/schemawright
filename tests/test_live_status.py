import json
from pathlib import Path
from schemawright.configwright_import import import_configwright
from schemawright.configwright_live_status import migration_status
from schemawright.files import checksum

class Cursor:
    def __init__(self,conn): self.conn=conn
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def execute(self,sql,params=()):
        assert sql.startswith(('SELECT ', 'SHOW ')), sql
        self.conn.queries.append(sql)
        self.sql=sql
    def fetchone(self): return (int(self.conn.exists),)
    def fetchall(self): return self.conn.history
class Connection:
    def __init__(self, exists=True, history=()):
        self.exists=exists; self.history=history; self.queries=[]; self.closed=False; self.rolled=False
    def cursor(self): return Cursor(self)
    def rollback(self): self.rolled=True
    def close(self): self.closed=True

def make_db(tmp_path):
    p=tmp_path/'db.json';p.write_text(json.dumps({'appdb': {'driver':'mysql','host':'127.0.0.1','port':3306,'database':'schemawright_test'}}))
    db=import_configwright(p,root=tmp_path,database='appdb').databases['appdb']
    db.migrations.active.mkdir(parents=True)
    f=db.migrations.active/'V0001__0010__create_table.sql'; f.write_text('SELECT 1;')
    return db,f

def scan(db,conn):
    result=migration_status(db,username='tester',password='secret',connect=lambda **kw: conn)
    assert conn.closed and conn.rolled
    return result

def test_missing_history_never_creates_table(tmp_path):
    db,f=make_db(tmp_path); conn=Connection(exists=False)
    result=scan(db,conn)
    assert result['items'][0]['state']=='history_unknown'
    assert not result['safeToPlan'] and not result['historyTableExists']
    assert all(q.startswith(('SELECT ', 'SHOW ')) for q in conn.queries)

def test_pending_when_history_exists(tmp_path):
    db,f=make_db(tmp_path)
    result=scan(db,Connection())
    assert result['items'][0]['state']=='pending' and result['safeToPlan']

def test_applied_and_checksum_drift(tmp_path):
    db,f=make_db(tmp_path)
    history=[('V',1,10,f.name,checksum(f),1)]
    assert scan(db,Connection(history=history))['items'][0]['state']=='applied'
    f.write_text('SELECT 2;')
    result=scan(db,Connection(history=history))
    assert result['items'][0]['state']=='checksum_drift' and not result['safeToPlan']

def test_missing_local_history(tmp_path):
    db,f=make_db(tmp_path)
    result=scan(db,Connection(history=[('V',3,10,'V0003__0010__gone.sql','abc',1)]))
    assert any(x['state']=='missing_local' for x in result['items'])
    assert not result['safeToPlan']

def test_failed_and_filename_drift(tmp_path):
    db,f=make_db(tmp_path)
    assert scan(db,Connection(history=[('V',1,10,f.name,checksum(f),0)]))['items'][0]['state']=='failed'
    assert scan(db,Connection(history=[('V',1,10,'different.sql',checksum(f),1)]))['items'][0]['state']=='filename_drift'
