import json
from pathlib import Path

import pytest
from captain_core.errors import ManifestError, ManifestOwnershipError
from schemawright.config import load_config


def project(root: Path, **sections):
    doc = {"schema": "captain/manifest/v1", "project": {"name": "sample", "type": "native", "version": "1.0.0"}, **sections}
    (root / "manifest.json").write_text(json.dumps(doc))


def config():
    return {"projectName": "sample", "databases": {"main": {"driver": "sqlite", "database": "db.sqlite"}}}


def test_inline(tmp_path):
    project(tmp_path, schemawright=config())
    result = load_config(tmp_path, canonical=True)
    assert result.project_name == "sample"
    assert result.databases["main"].migrations.active == tmp_path / "sql/migrations/active/main"


def test_split(tmp_path):
    (tmp_path / "manifests").mkdir()
    (tmp_path / "manifests/schemawright.json").write_text(json.dumps({"schema": "captain/tool-manifest/v1", "tool": "schemawright", "config": config()}))
    project(tmp_path, manifests={"schemawright": "manifests/schemawright.json"})
    assert "main" in load_config(tmp_path, canonical=True).databases


def test_hybrid_other_tool(tmp_path):
    project(tmp_path, schemawright=config(), stager={"enabled": True})
    assert "main" in load_config(tmp_path, canonical=True).databases


def test_duplicate_rejected(tmp_path):
    project(tmp_path, schemawright=config(), manifests={"schemawright": "manifests/schemawright.json"})
    with pytest.raises(ManifestError):
        load_config(tmp_path, canonical=True)


def test_foreign_owner_rejected(tmp_path):
    (tmp_path / "other.json").write_text(json.dumps({"schema": "captain/tool-manifest/v1", "tool": "dockhand", "config": config()}))
    project(tmp_path, manifests={"schemawright": "other.json"})
    with pytest.raises(ManifestOwnershipError):
        load_config(tmp_path, canonical=True)


def test_path_traversal_rejected(tmp_path):
    project(tmp_path, manifests={"schemawright": "../other.json"})
    with pytest.raises(ManifestError):
        load_config(tmp_path, canonical=True)


def test_legacy_unchanged(tmp_path):
    (tmp_path / "schemawright.json").write_text(json.dumps(config()))
    assert "main" in load_config(tmp_path / "schemawright.json").databases
