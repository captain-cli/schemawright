"""Regression tests for bounded 4-6 digit version/order identifiers."""
from pathlib import Path
import pytest

from schemawright.model import format_migration_filename, parse_migration_filename
from schemawright.sql_intake import scan_sql_tree


@pytest.mark.parametrize('version,order', [
    (1, 10), (1, 10000), (1, 999999), (999999, 999999), (10000, 100000),
])
def test_format_parse_roundtrip(version, order):
    filename = format_migration_filename('V', version, order, 'create_table')
    parsed = parse_migration_filename(Path(filename))
    assert parsed is not None
    assert (parsed.version, parsed.install_order) == (version, order)


def test_old_four_digit_format_preserved():
    assert format_migration_filename('V', 1, 10, 'create_table') == 'V0001__0010__create_table.sql'


@pytest.mark.parametrize('filename', [
    'V0000001__0010__create_table.sql',
    'V0001__0000001__create_table.sql',
    'V001__0010__create_table.sql',
    'V0001__010__create_table.sql',
])
def test_parser_rejects_unsupported_widths(filename):
    assert parse_migration_filename(Path(filename)) is None


@pytest.mark.parametrize('version,order', [(1000000, 10), (1, 1000000), (-1, 10), (1, -1)])
def test_formatter_rejects_out_of_range(version, order):
    with pytest.raises(ValueError):
        format_migration_filename('V', version, order, 'table')


def test_intake_accepts_six_digit_order(tmp_path):
    sql = tmp_path / 'sql' / 'crispy-database' / 'migrations' / 'audio'
    sql.mkdir(parents=True)
    (sql / 'V0001__100000__large_table.sql').write_text('SELECT 1;')
    report = scan_sql_tree(tmp_path)
    assert report['readyForConversion']
    assert report['counts']['compliant'] == 1


def test_legacy_intake_expands_order_to_six_digits(tmp_path):
    sql = tmp_path / 'sql' / 'crispy-database' / 'migrations' / 'audio'
    sql.mkdir(parents=True)
    (sql / 'V10000__large_table.sql').write_text('SELECT 1;')
    report = scan_sql_tree(tmp_path)
    assert report['readyForConversion']
    assert report['files'][0]['proposedName'] == 'V0001__100000__large_table.sql'


def test_legacy_intake_rejects_order_over_six_digits(tmp_path):
    sql = tmp_path / 'sql' / 'audio'
    sql.mkdir(parents=True)
    (sql / 'V100000__too_high.sql').write_text('SELECT 1;')
    report = scan_sql_tree(tmp_path)
    assert not report['readyForConversion']
    assert any(i['code'] == 'legacy_order_out_of_range' for i in report['issues'])
