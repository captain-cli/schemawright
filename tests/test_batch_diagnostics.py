"""Phase 6E.2: structured, credential-safe failure classification."""
import pytest

from schemawright.migration_batch import migrate_batch, MigrationBatchError
from test_migration_batch import setup, Conn, Cursor, run, record


def test_authentication_error_redacted(tmp_path):
    db, _ = setup(tmp_path)
    def fail(**_):
        raise RuntimeError(1045, 'password secret connection denied')
    with pytest.raises(MigrationBatchError) as err:
        migrate_batch(db, username='schemawright_migrator', password='secret', connect=fail)
    r=err.value.as_result()
    assert r['errorCode']=='authentication_failed' and r['stage']=='connect'
    assert r['changesExecuted'] is False and r['manualInspectionRequired'] is False
    assert 'secret' not in str(r)


def test_unreachable_server_redacted(tmp_path):
    db, _=setup(tmp_path)
    def fail(**_): raise RuntimeError(2003, 'password secret: 127.0.0.1')
    with pytest.raises(MigrationBatchError) as err:
        migrate_batch(db, username='schemawright_migrator', password='secret', connect=fail)
    r=err.value.as_result()
    assert r['errorCode']=='connection_failed' and r['changesExecuted'] is False
    assert not r['manualInspectionRequired'] and 'secret' not in str(r)


def test_lock_unavailable_no_write(tmp_path):
    db, _=setup(tmp_path)
    class LockCursor(Cursor):
        def fetchone(self):
            if 'GET_LOCK' in self.last: return (0,)
            return super().fetchone()
    class LockConn(Conn):
        def cursor(self): return LockCursor(self)
    with pytest.raises(MigrationBatchError) as err: run(db, LockConn())
    r=err.value.as_result()
    assert r['errorCode']=='lock_unavailable' and r['stage']=='lock'
    assert r['changesExecuted'] is False and not r['manualInspectionRequired']


def test_metadata_permission_error_no_write(tmp_path):
    db, _=setup(tmp_path)
    class DeniedCursor(Cursor):
        def execute(self,sql,params=()):
            if 'information_schema.TABLES' in sql: raise RuntimeError(1142,'secret denied')
            super().execute(sql,params)
    class DeniedConn(Conn):
        def cursor(self): return DeniedCursor(self)
    with pytest.raises(MigrationBatchError) as err: run(db,DeniedConn())
    r=err.value.as_result()
    assert r['errorCode']=='permission_denied' and r['stage']=='schema_inspection'
    assert r['changesExecuted'] is False and not r['manualInspectionRequired']
    assert 'secret' not in str(r)


def test_unrecorded_table_is_drift_with_review_required(tmp_path):
    db,_=setup(tmp_path)
    with pytest.raises(MigrationBatchError) as err: run(db,Conn(tables={'probe_2'}))
    r=err.value.as_result()
    assert r['errorCode']=='schema_drift' and r['manualInspectionRequired']
    assert r['changesExecuted'] is False


def test_ddl_failure_uncertain_and_redacted(tmp_path):
    db,_=setup(tmp_path)
    with pytest.raises(MigrationBatchError) as err:
        run(db,Conn(fail_at=1),approved=True,dry_run=False)
    r=err.value.as_result()
    assert r['errorCode']=='execution_uncertain' and r['stage']=='ddl'
    assert r['changesExecuted'] is None and r['manualInspectionRequired']
    assert r['completed']==[] and 'secret' not in str(r)


def test_completed_then_ddl_failure_preserves_progress(tmp_path):
    db,files=setup(tmp_path)
    with pytest.raises(MigrationBatchError) as err:
        run(db,Conn(fail_at=2),approved=True,dry_run=False)
    r=err.value.as_result()
    assert r['errorCode']=='execution_uncertain'
    assert r['completed']==[files[0].name]
    assert r['changesExecuted'] is True and r['manualInspectionRequired']


def test_history_insert_error_is_uncertain(tmp_path):
    db,_=setup(tmp_path)
    with pytest.raises(MigrationBatchError) as err:
        run(db,Conn(fail_insert=True),approved=True,dry_run=False)
    r=err.value.as_result()
    assert r['errorCode']=='execution_uncertain' and r['stage']=='history_write'
    assert r['changesExecuted'] is None and r['manualInspectionRequired']


def test_up_to_date_still_works(tmp_path):
    db,files=setup(tmp_path)
    c=Conn(tables={'probe_1','probe_2','probe_3'},history=[record(f,i) for i,f in enumerate(files,1)])
    r=run(db,c,max_migrations=25)
    assert r['action']=='up-to-date' and not r['changesExecuted']
