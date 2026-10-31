import json
from pathlib import Path

import pytest

from schemawright.cli import main
from schemawright.configwright_import import import_configwright
from schemawright.configwright_credentials import resolve_database_credentials, CredentialResolutionError


def setup(tmp_path, names=None):
    raw = {"appdb": {"driver": "mysql", "database": "demo", "passwordRef": "env:APP_DB_PASSWORD", "tokenRef": "env:API_TOKEN"}}
    path = tmp_path / "databases.json"
    path.write_text(json.dumps(raw))
    db = import_configwright(path, root=tmp_path).databases["appdb"]
    return path, db


def test_resolves_without_mutating(tmp_path):
    _, db = setup(tmp_path)
    secrets = tmp_path / "secrets.env"
    secrets.write_text('APP_DB_PASSWORD="very-secret"\nAPI_TOKEN=token123\n')
    original = db.settings.copy()
    assert resolve_database_credentials(db, secrets) == {"passwordRef": "very-secret", "tokenRef": "token123"}
    assert db.settings == original


def test_missing_key_fails_closed(tmp_path):
    _, db = setup(tmp_path)
    secrets = tmp_path / "secrets.env"
    secrets.write_text("APP_DB_PASSWORD=secret\n")
    with pytest.raises(CredentialResolutionError, match="API_TOKEN"):
        resolve_database_credentials(db, secrets)


def test_empty_value_fails_closed(tmp_path):
    _, db = setup(tmp_path)
    secrets = tmp_path / "secrets.env"
    secrets.write_text("APP_DB_PASSWORD=\nAPI_TOKEN=present\n")
    with pytest.raises(CredentialResolutionError, match="APP_DB_PASSWORD"):
        resolve_database_credentials(db, secrets)


def test_duplicate_key_fails_closed(tmp_path):
    _, db = setup(tmp_path)
    secrets = tmp_path / "secrets.env"
    secrets.write_text("APP_DB_PASSWORD=one\nAPP_DB_PASSWORD=two\nAPI_TOKEN=value\n")
    with pytest.raises(CredentialResolutionError, match="Duplicate"):
        resolve_database_credentials(db, secrets)


def test_unreadable_file_safe_error(tmp_path):
    _, db = setup(tmp_path)
    with pytest.raises(CredentialResolutionError, match="unavailable") as error:
        resolve_database_credentials(db, tmp_path / "missing.env")
    assert str(tmp_path) not in str(error.value)


def test_cli_does_not_show_values(tmp_path, capsys):
    src, _ = setup(tmp_path)
    secrets = tmp_path / "secrets.env"
    secrets.write_text("APP_DB_PASSWORD=top-secret-sentinel\nAPI_TOKEN=hidden-sentinel\n")
    args = ["configwright-credentials-check", "--source", str(src), "--project-root", str(tmp_path), "--secrets-file", str(secrets)]
    assert main(args) == 0
    output = capsys.readouterr()
    assert "2 credential reference(s) resolved" in output.out
    assert "sentinel" not in output.out + output.err
    assert main(args[:-1] + [str(tmp_path / 'missing.env')]) == 1
    output = capsys.readouterr()
    assert "sentinel" not in output.out + output.err


def test_invalid_assignment_never_echoes_value(tmp_path):
    _, db = setup(tmp_path)
    secrets = tmp_path / "secrets.env"
    secrets.write_text("INVALID SECRET=super-secret-sentinel\n")
    with pytest.raises(CredentialResolutionError) as error:
        resolve_database_credentials(db, secrets)
    assert "sentinel" not in str(error.value)
