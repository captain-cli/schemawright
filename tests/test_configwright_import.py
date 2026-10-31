import json
from pathlib import Path

import pytest

from schemawright.cli import main
from schemawright.configwright_import import import_configwright


def fixture(tmp_path, raw):
    src = tmp_path / "databases.json"
    src.write_text(json.dumps(raw), encoding="utf-8")
    return src


def test_import_into_existing_model(tmp_path):
    src = fixture(tmp_path, {"appdb": {"driver": "mysql", "database": "demo", "passwordRef": "env:APP_DB_PASSWORD"}})
    cfg = import_configwright(src, root=tmp_path)
    db = cfg.databases["appdb"]
    assert db.name == "appdb" and db.driver == "mysql"
    assert db.settings["passwordRef"] == "env:APP_DB_PASSWORD"
    assert db.migrations.active == (tmp_path / "sql/migrations/active/appdb").resolve()
    assert cfg.config_path == src


def test_selected_database(tmp_path):
    src = fixture(tmp_path, {"a": {"driver": "sqlite", "database": "a.sqlite"}, "b": {"driver": "sqlite", "database": "b.sqlite"}})
    assert list(import_configwright(src, root=tmp_path, database="b").databases) == ["b"]


def test_unknown_database_rejected(tmp_path):
    src = fixture(tmp_path, {"a": {"driver": "sqlite", "database": "a.sqlite"}})
    with pytest.raises(ValueError, match="Unknown ConfigWright database"):
        import_configwright(src, root=tmp_path, database="missing")


def test_empty_input_rejected(tmp_path):
    with pytest.raises(ValueError, match="contains no databases"):
        import_configwright(fixture(tmp_path, {}), root=tmp_path)


def test_read_only_command_and_no_secret_output(tmp_path, capsys):
    src = fixture(tmp_path, {"appdb": {"driver": "mysql", "database": "demo", "passwordRef": "env:APP_DB_PASSWORD"}})
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob('*'))
    assert main(["configwright-import", "--source", str(src), "--project-root", str(tmp_path)]) == 0
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob('*')) == before
    stdout = capsys.readouterr().out
    assert "appdb: mysql" in stdout and "APP_DB_PASSWORD" not in stdout


def test_invalid_password_rejected(tmp_path, capsys):
    src = fixture(tmp_path, {"appdb": {"driver": "mysql", "database": "demo", "password": "abc"}})
    assert main(["configwright-import", "--source", str(src), "--project-root", str(tmp_path)]) == 1
    assert "literal password forbidden" in capsys.readouterr().err
