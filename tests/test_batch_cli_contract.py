"""Phase 6E.3: prove Captain-facing JSON/error exit boundaries without a DB server."""
import json
import pytest
from schemawright import cli
from schemawright.migration_batch import MigrationBatchError
from test_migration_batch import setup, Conn, Cursor, run


def args():
    return ['configwright-migrate-batch','--source','fake.json','--project-root','/tmp',
            '--database','appdb','--login-user','schemawright_migrator','--max-migrations','2']


def cli_fixture(monkeypatch, tmp_path):
    db, files = setup(tmp_path)
    class Imported:
        databases={'appdb':db}
    monkeypatch.setattr(cli, 'import_configwright', lambda *a, **k: Imported())
    monkeypatch.setattr(cli.getpass,'getpass',lambda *_: 'secret')
    return db,files


def test_cli_success_machine_json_stdout(monkeypatch,tmp_path,capsys):
    db,_=cli_fixture(monkeypatch,tmp_path)
    monkeypatch.setattr(cli,'migrate_batch',lambda *a,**k: {
        'action':'would-apply','pendingBefore':2,'selected':['a','b'],
        'completed':[],'changesExecuted':False})
    assert cli.main(args())==0
    output=capsys.readouterr()
    assert json.loads(output.out)['action']=='would-apply'
    assert output.err==''


def test_cli_authentication_error_stderr_json_exit_2(monkeypatch,tmp_path,capsys):
    cli_fixture(monkeypatch,tmp_path)
    def fail(*_a,**_k):
        raise MigrationBatchError('Batch database operation failed; check errorCode and stage',
                                  error_code='authentication_failed',stage='connect')
    monkeypatch.setattr(cli,'migrate_batch',fail)
    assert cli.main(args())==2
    out=capsys.readouterr()
    r=json.loads(out.err)
    assert out.out=='' and r['errorCode']=='authentication_failed'
    assert r['changesExecuted'] is False and r['manualInspectionRequired'] is False


def test_cli_uncertain_outcome_json_null_prevents_auto_retry(monkeypatch,tmp_path,capsys):
    cli_fixture(monkeypatch,tmp_path)
    def fail(*_a,**_k):
        raise MigrationBatchError('DDL or history write may have committed; manual recovery inspection required',
                                  error_code='execution_uncertain',stage='history_write',
                                  changes_executed=None,manual_inspection_required=True)
    monkeypatch.setattr(cli,'migrate_batch',fail)
    assert cli.main(args())==2
    r=json.loads(capsys.readouterr().err)
    assert r['changesExecuted'] is None
    assert r['manualInspectionRequired'] is True and r['completed']==[]
    assert r['errorCode']=='execution_uncertain'


def test_cli_denied_authorization_is_json_and_no_password(monkeypatch,tmp_path,capsys):
    cli_fixture(monkeypatch,tmp_path)
    monkeypatch.setattr('builtins.input',lambda *_:'NO')
    monkeypatch.setattr(cli.getpass,'getpass',lambda *_:pytest.fail('must not prompt for password'))
    assert cli.main(args()+['--apply'])==2
    r=json.loads(capsys.readouterr().err)
    assert r['errorCode']=='authorization_declined' and r['changesExecuted'] is False
    assert not r['manualInspectionRequired']


def test_cli_configuration_failure_is_json(monkeypatch,capsys):
    monkeypatch.setattr(cli,'import_configwright',lambda *a,**k: (_ for _ in ()).throw(ValueError('secret')))
    assert cli.main(args())==2
    r=json.loads(capsys.readouterr().err)
    assert r['errorCode']=='configuration_or_input_error'
    assert r['changesExecuted'] is False and 'secret' not in str(r)


def test_failed_second_ddl_retains_completed_and_uncertainty(tmp_path):
    db,files=setup(tmp_path)
    with pytest.raises(MigrationBatchError) as exc:
        run(db,Conn(fail_at=2),approved=True,dry_run=False,max_migrations=2)
    result=exc.value.as_result()
    assert result['completed']==[files[0].name]
    assert result['errorCode']=='execution_uncertain'
    assert result['manualInspectionRequired']


def test_history_failure_stops_before_next_migration(tmp_path):
    db,files=setup(tmp_path)
    conn=Conn(fail_insert=True)
    with pytest.raises(MigrationBatchError) as exc:
        run(db,conn,approved=True,dry_run=False,max_migrations=3)
    assert conn.ddl_count==1
    assert exc.value.as_result()['stage']=='history_write'
    assert exc.value.as_result()['changesExecuted'] is None


def test_auth_failure_never_reaches_db_cursor(tmp_path):
    db,_=setup(tmp_path)
    calls=[]
    def denied(**opts):
        calls.append('connect')
        raise RuntimeError(1045,'private password')
    from schemawright.migration_batch import migrate_batch
    with pytest.raises(MigrationBatchError) as exc:
        migrate_batch(db,username='schemawright_migrator',password='private',connect=denied)
    r=exc.value.as_result()
    assert calls==['connect'] and r['errorCode']=='authentication_failed'
    assert not r['manualInspectionRequired']


def test_cli_error_schema_has_stable_required_keys():
    r=MigrationBatchError('safe message',error_code='lock_unavailable',stage='lock').as_result()
    assert set(r)=={'action','errorCode','stage','completed','changesExecuted','manualInspectionRequired','error'}
