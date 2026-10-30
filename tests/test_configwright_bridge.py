import json
from schemawright.cli import main
from schemawright.configwright_bridge import inspect_databases
import pytest


def write(tmp_path, raw):
    path = tmp_path / 'databases.json'
    path.write_text(json.dumps(raw), encoding='utf-8')
    return path


def test_valid_configwright_database(tmp_path, capsys):
    path = write(tmp_path, {'appdb': {'driver': 'mysql', 'host': '127.0.0.1', 'database': 'app', 'passwordRef': 'env:APP_DB_PASSWORD'}})
    assert main(['configwright-check', '--source', str(path)]) == 0
    output = capsys.readouterr().out
    assert 'appdb: mysql' in output and 'passwordRef' in output
    assert 'APP_DB_PASSWORD' not in output


def test_missing_database_target(tmp_path):
    with pytest.raises(ValueError, match='missing database'):
        inspect_databases(write(tmp_path, {'app': {'driver': 'mysql'}}))


def test_literal_password_rejected(tmp_path):
    with pytest.raises(ValueError, match='literal password forbidden'):
        inspect_databases(write(tmp_path, {'app': {'driver': 'mysql', 'database': 'a', 'password': 'unsafe'}}))


def test_bad_credential_reference_rejected(tmp_path):
    with pytest.raises(ValueError, match='env:NAME'):
        inspect_databases(write(tmp_path, {'app': {'driver': 'mysql', 'database': 'a', 'passwordRef': 'plain:secret'}}))


def test_invalid_name_rejected(tmp_path):
    with pytest.raises(ValueError, match='Invalid database identifier'):
        inspect_databases(write(tmp_path, {'../bad': {'driver': 'sqlite', 'database': 'a.sqlite'}}))


def test_no_config_file_created(tmp_path):
    path = write(tmp_path, {'app': {'driver': 'sqlite', 'database': 'a.sqlite'}})
    before = list(tmp_path.iterdir())
    assert main(['configwright-check', '--source', str(path)]) == 0
    assert list(tmp_path.iterdir()) == before
