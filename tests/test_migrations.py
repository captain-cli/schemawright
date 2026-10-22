from pathlib import Path

from schemawright.migrations import discover_sql_files


def test_discover_versioned_migrations(tmp_path: Path):
    (tmp_path / "V0002__0020__second.sql").write_text("SELECT 2;")
    (tmp_path / "V0001__0010__first.sql").write_text("SELECT 1;")
    (tmp_path / "bad.sql").write_text("SELECT 0;")

    files = discover_sql_files(tmp_path, "V")

    assert [file.version for file in files] == [1, 2]
    assert files[0].description == "first"
