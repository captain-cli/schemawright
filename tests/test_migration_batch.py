import json
from pathlib import Path
import pytest
from schemawright.configwright_import import import_configwright
from schemawright.migration_batch import migrate_batch, MigrationBatchError
from schemawright.files import checksum


def setup(tmp_path, n=3):
    cfg=tmp_path/'databases.json'
    cfg.write_text(json.dumps({'appdb':{'database':'schemawright_test','driver':'mysql','host':'127.0.0.1','port':3306}}))
    db=import_configwright(cfg,root=tmp_path,database='appdb').databases['appdb']
    db.migrations.active.mkdir(parents=True)
    files=[]
    for i in range(1,n+1):
        p=db.migrations.active/f'V{i:04d}__0010__create_probe_{i}.sql'
        p.write_text(f'CREATE TABLE probe_{i} (id BIGINT NOT NULL PRIMARY KEY) ENGINE=InnoDB;\n')
        files.append(p)
    return db,files


class Cursor:
    def __init__(self,c):self.c=c
    def __enter__(self):return self
    def __exit__(self,*_):return False
    def execute(self,sql,params=()):
        self.c.queries.append((sql,params));self.last=sql;self.params=params
        if sql.startswith('CREATE TABLE'):
            self.c.ddl_count+=1
            if self.c.fail_at == self.c.ddl_count:
                raise RuntimeError('private password')
            table=sql.split('(')[0].split()[-1]
            self.c.tables.add(table)
        elif sql.startswith('INSERT INTO'):
            if self.c.fail_insert:
                raise RuntimeError('private password')
            self.c.history.append(('V',params[1],params[2],params[4],params[5],1))
    def fetchone(self):
        if 'GET_LOCK' in self.last:return (1,)
        if 'COUNT(*)' in self.last:return (int(self.params[1] in self.c.tables),)
        raise AssertionError(self.last)
    def fetchall(self):
        if 'information_schema.TABLES' in self.last:
            return [('schema_migrations','BASE TABLE')]+[(t,'BASE TABLE') for t in sorted(self.c.tables)]
        if 'schema_migrations' in self.last:return list(self.c.history)
        raise AssertionError(self.last)


class Conn:
    def __init__(self,tables=(),history=(),fail_at=0,fail_insert=False):
        self.tables=set(tables);self.history=list(history);self.fail_at=fail_at
        self.fail_insert=fail_insert;self.ddl_count=0;self.queries=[];self.closed=False
    def cursor(self):return Cursor(self)
    def close(self):self.closed=True


def run(db,conn,**kw):
    return migrate_batch(db,username='schemawright_migrator',password='secret',connect=lambda **_ : conn,**kw)


def record(p,i):return ('V',i,10,p.name,checksum(p),1)


def test_preview_full_batch_no_writes(tmp_path):
    db,files=setup(tmp_path)
    c=Conn();r=run(db,c)
    assert r['action']=='would-apply' and len(r['selected'])==3
    assert r['pendingBefore']==3 and r['changesExecuted'] is False
    assert not any(s.startswith(('CREATE','INSERT')) for s,_ in c.queries)
    assert c.closed


def test_ordered_batch_and_repeat_up_to_date(tmp_path):
    db,files=setup(tmp_path)
    c=Conn()
    r=run(db,c,approved=True,dry_run=False)
    assert r['completed']==[p.name for p in files]
    assert len(c.history)==3 and c.ddl_count==3
    c2=Conn(tables=c.tables,history=c.history)
    assert run(db,c2)['action']=='up-to-date'


def test_respects_limit(tmp_path):
    db,files=setup(tmp_path)
    c=Conn();r=run(db,c,approved=True,dry_run=False,max_migrations=2)
    assert len(r['completed'])==2 and r['remaining']==1
    assert c.ddl_count==2


def test_stop_at_first_failed_ddl(tmp_path):
    db,files=setup(tmp_path)
    c=Conn(fail_at=2)
    with pytest.raises(MigrationBatchError) as exc:
        run(db,c,approved=True,dry_run=False)
    assert exc.value.completed==[files[0].name]
    assert c.ddl_count==2 and len(c.history)==1
    assert 'private' not in str(exc.value)


def test_history_insert_failure_never_continues(tmp_path):
    db,files=setup(tmp_path)
    c=Conn(fail_insert=True)
    with pytest.raises(MigrationBatchError, match='manual'):
        run(db,c,approved=True,dry_run=False)
    assert c.ddl_count==1 and not c.history


def test_refuse_unrecorded_existing_table(tmp_path):
    db,files=setup(tmp_path)
    c=Conn(tables={'probe_2'})
    with pytest.raises(MigrationBatchError, match='Unrecorded'):
        run(db,c,approved=True,dry_run=False)
    assert c.ddl_count==0


def test_refuse_history_gap(tmp_path):
    db,files=setup(tmp_path)
    c=Conn(tables={'probe_2'},history=[record(files[1],2)])
    with pytest.raises(MigrationBatchError,match='ordering gap'):
        run(db,c)


def test_refuse_drift(tmp_path):
    db,files=setup(tmp_path)
    c=Conn(tables={'probe_1'},history=[('V',1,10,files[0].name,'f'*64,1)])
    with pytest.raises(MigrationBatchError,match='drifted'):
        run(db,c)


def test_no_permission_to_apply_without_approval(tmp_path):
    db,_=setup(tmp_path)
    with pytest.raises(MigrationBatchError,match='authorization'):
        run(db,Conn(),dry_run=False)


def test_refuse_duplicate_target(tmp_path):
    db,files=setup(tmp_path)
    files[1].write_text(files[0].read_text())
    with pytest.raises(MigrationBatchError,match='Duplicate'):
        run(db,Conn())


def test_refuse_dangerous_sql(tmp_path):
    db,files=setup(tmp_path)
    files[2].write_text('DROP TABLE other;')
    with pytest.raises(MigrationBatchError,match='unsupported'):
        run(db,Conn())


def test_refuse_invalid_limit_and_app_login(tmp_path):
    db,_=setup(tmp_path)
    for n in (0,26,True):
        with pytest.raises(MigrationBatchError):run(db,Conn(),max_migrations=n)
    with pytest.raises(MigrationBatchError):
        migrate_batch(db,username='schemawright_app',password='secret')
