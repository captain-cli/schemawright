import json

from schemawright.cli import main


def source(tmp_path, name='appdb'):
    p = tmp_path / 'databases.json'
    p.write_text(json.dumps({name: {'driver': 'sqlite', 'database': 'app.sqlite', 'passwordRef': 'env:APP_DB_PASSWORD'}}))
    return p


def plan(tmp_path, src=None):
    src = src or source(tmp_path)
    return main(['configwright-plan', '--source', str(src), '--project-root', str(tmp_path), '--database', 'appdb'])


def test_empty_project_offline(tmp_path, capsys):
    assert plan(tmp_path) == 0
    assert '0 SQL file(s)' in capsys.readouterr().out
    assert not (tmp_path / 'sql').exists()


def test_orders_active_and_staged_files(tmp_path, capsys):
    root = tmp_path / 'sql/migrations'
    for lane, name in [('staged', 'V0002__0010__add_column.sql'), ('active', 'V0001__0010__create_table.sql')]:
        path = root / lane / 'appdb' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('-- example')
    assert plan(tmp_path) == 0
    output = capsys.readouterr().out
    assert output.index('V0001') < output.index('V0002')
    assert 'applied state unknown' in output


def test_invalid_filename_fails_closed(tmp_path, capsys):
    path = tmp_path / 'sql/migrations/active/appdb/bad.sql'
    path.parent.mkdir(parents=True)
    path.write_text('-- invalid')
    assert plan(tmp_path) == 1
    assert 'invalid SQL filename' in capsys.readouterr().err


def test_duplicate_order_fails_closed(tmp_path, capsys):
    directory = tmp_path / 'sql/migrations/active/appdb'
    directory.mkdir(parents=True)
    (directory / 'V0001__0010__first.sql').write_text('-- first')
    (directory / 'V0001__0010__second.sql').write_text('-- second')
    assert plan(tmp_path) == 1
    assert 'duplicate version/order' in capsys.readouterr().err


def test_wrong_prefix_fails_closed(tmp_path, capsys):
    directory = tmp_path / 'sql/migrations/active/appdb'
    directory.mkdir(parents=True)
    (directory / 'S0001__0010__seed.sql').write_text('-- misplaced')
    assert plan(tmp_path) == 1
    assert 'unexpected prefix' in capsys.readouterr().err


def test_missing_database_fails_closed(tmp_path, capsys):
    assert plan(tmp_path, source(tmp_path, 'other')) == 1
    assert 'Unknown ConfigWright database' in capsys.readouterr().err
