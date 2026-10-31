from pathlib import Path
import json
from schemawright.configwright_import import import_configwright
from schemawright.owner_provision import plan_owner_provision, OwnerProvisionError
from schemawright.cli import main
import pytest


def _fixture(tmp_path):
    source = tmp_path / 'databases.json'
    source.write_text(json.dumps({'appdb': {'driver':'mysql','database':'demo','host':'localhost','passwordRef':'env:APP_DB_PASSWORD'}}))
    db = import_configwright(source, root=tmp_path, database='appdb').databases['appdb']
    return source,db


def test_adoption_never_creates_database(tmp_path):
    _, db = _fixture(tmp_path)
    p = plan_owner_provision(db, existing_database=True, application_user='crispy_app')
    assert not p.create_database and p.create_application_user and not p.applied


def test_fresh_install_requires_database_creation(tmp_path):
    _,db = _fixture(tmp_path)
    p = plan_owner_provision(db, existing_database=False, application_user='crispy_app')
    assert p.create_database


def test_existing_user_reuse(tmp_path):
    _,db = _fixture(tmp_path)
    p = plan_owner_provision(db, existing_database=True, existing_user=True, application_user='crispy_app')
    assert not p.create_application_user


def test_reject_invalid_account(tmp_path):
    _,db=_fixture(tmp_path)
    with pytest.raises(OwnerProvisionError):
        plan_owner_provision(db, existing_database=True, application_user='db_admin;DROP')


def test_reject_user_on_new_db(tmp_path):
    _,db=_fixture(tmp_path)
    with pytest.raises(OwnerProvisionError):
        plan_owner_provision(db, existing_database=False, existing_user=True, application_user='crispy')


def test_cli_is_offline_and_secret_free(tmp_path, capsys):
    source,_=_fixture(tmp_path)
    rc=main(['configwright-owner-plan','--source',str(source),'--project-root',str(tmp_path), '--database','appdb', '--existing-database','--application-user','crispy_app'])
    captured=capsys.readouterr()
    assert rc == 0
    assert 'preserve-existing' in captured.out
    assert 'APP_DB_PASSWORD' not in captured.out
    assert 'executed' in captured.out and 'false' in captured.out


def test_cli_requires_mode(tmp_path):
    source,_=_fixture(tmp_path)
    with pytest.raises(SystemExit):
        main(['configwright-owner-plan','--source',str(source),'--project-root',str(tmp_path), '--database','appdb','--application-user','crispy_app'])
