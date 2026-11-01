import json
import pytest
from schemawright import cli
from schemawright.configwright_import import import_configwright
from schemawright.owner_preflight import preflight_owner_provision, OwnerPreflightError


def make_db(tmp_path):
    file = tmp_path / 'db.json'
    file.write_text(json.dumps({'appdb': {'driver': 'mysql', 'database': 'schemawright_test',
                                         'host': '127.0.0.1', 'port': 3306}}))
    return file, import_configwright(file, root=tmp_path, database='appdb').databases['appdb']


class Cursor:
    def __init__(self, conn): self.conn = conn
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, query, params=()):
        self.conn.queries.append(query)
        self.query = query
    def fetchone(self):
        if self.query.startswith('SELECT VERSION'): return ('8.0.46',)
        return (int(self.conn.exists),)
    def fetchall(self): return [('GRANT SELECT ON schemawright_test.*',)]


class Conn:
    def __init__(self, exists): self.exists=exists; self.queries=[]; self.closed=False
    def cursor(self): return Cursor(self)
    def rollback(self): pass
    def close(self): self.closed=True


@pytest.mark.parametrize('exists,adopt,action', [(True,True,'preserve-existing'),(False,False,'create-after-approval')])
def test_matching_state(tmp_path,exists,adopt,action):
    _, db = make_db(tmp_path)
    c=Conn(exists)
    outcome=preflight_owner_provision(db,username='inspector',password='secret',
        existing_database=adopt,application_user='crispy_app',connect=lambda **kw: c)
    assert outcome.database_action==action and not outcome.authorized and not outcome.executed
    assert c.closed and all(q.startswith(('SELECT ','SHOW ')) for q in c.queries)


@pytest.mark.parametrize('exists,adopt',[(False,True),(True,False)])
def test_mismatched_state_rejected(tmp_path,exists,adopt):
    _, db=make_db(tmp_path)
    c=Conn(exists)
    with pytest.raises(OwnerPreflightError):
        preflight_owner_provision(db,username='inspector',password='secret',
            existing_database=adopt,application_user='crispy_app',connect=lambda **kw:c)
    assert c.closed


def test_failure_redacts_driver_exception(tmp_path):
    _,db=make_db(tmp_path)
    def fail(**kw): raise RuntimeError('credential=super-secret')
    with pytest.raises(OwnerPreflightError) as e:
        preflight_owner_provision(db,username='inspector',password='super-secret',
            existing_database=True,application_user='crispy_app',connect=fail)
    assert 'super-secret' not in str(e.value)


def test_cli_prompt_and_no_execution(tmp_path,monkeypatch,capsys):
    source,_=make_db(tmp_path)
    monkeypatch.setattr(cli.getpass,'getpass',lambda x: 'private')
    from schemawright.owner_preflight import OwnerPreflight
    def fake(db,**kwargs):
        assert kwargs['password']=='private'
        return OwnerPreflight('appdb','8.0',True,'preserve-existing',
                              'create-after-approval','crispy_app',('application-dml',))
    monkeypatch.setattr(cli,'preflight_owner_provision',fake)
    args=['configwright-owner-preflight','--source',str(source),'--project-root',str(tmp_path),
          '--database','appdb','--login-user','inspector','--existing-database',
          '--application-user','crispy_app']
    assert cli.main(args)==0
    stdout=capsys.readouterr().out
    assert 'private' not in stdout and '"executed": false' in stdout
    assert '"accountExistenceVerified": false' in stdout
