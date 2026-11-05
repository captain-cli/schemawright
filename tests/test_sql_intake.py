"""Phase 7B.1 offline intake and fail-closed inventory tests."""
from pathlib import Path
import pytest
from schemawright.sql_intake import scan_sql_tree


def add(root, filename, data='SELECT 1;'):
    p=root/'sql'/filename
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(data)
    return p


def test_legacy_migrations_map_to_one_baseline(tmp_path):
    add(tmp_path,'crispy-database/migrations/audio/V001__artist.sql')
    add(tmp_path,'crispy-database/migrations/audio/V002__genre.sql')
    result=scan_sql_tree(tmp_path)
    assert result['readyForConversion']
    assert [f['proposedName'] for f in result['files']] == [
        'V0001__0010__artist.sql', 'V0001__0020__genre.sql']
    assert result['readOnly'] and result['changesExecuted'] is False


def test_compliant_filename_is_left_untouched(tmp_path):
    add(tmp_path,'users/V0002__0010__accounts.sql')
    report=scan_sql_tree(tmp_path)
    assert report['readyForConversion']
    assert report['counts']['compliant']==1
    assert report['files'][0]['proposedName']=='V0002__0010__accounts.sql'


def test_unordered_seed_is_not_guessed(tmp_path):
    add(tmp_path,'seeds/authentication-config/application_settings.sql')
    report=scan_sql_tree(tmp_path)
    assert not report['readyForConversion']
    assert report['files'][0]['proposedName'] is None
    assert 'unrecognized_filename_manual_order_required' in {i['code'] for i in report['issues']}


def test_archive_is_not_scheduled(tmp_path):
    add(tmp_path,'crispy-database/migrations/archives/V001__old.sql')
    report=scan_sql_tree(tmp_path)
    assert report['readyForConversion']
    assert report['files'][0]['status']=='archived'
    assert report['files'][0]['proposedName'] is None


def test_duplicate_identity_is_rejected(tmp_path):
    add(tmp_path,'audio/V001__artist.sql')
    add(tmp_path,'audio/V001__genre.sql')
    report=scan_sql_tree(tmp_path)
    assert not report['readyForConversion']
    assert 'duplicate_version_order' in {i['code'] for i in report['issues']}


def test_destination_collision_is_rejected(tmp_path):
    add(tmp_path,'audio/V001__artist.sql')
    add(tmp_path,'audio/V0001__0010__artist.sql')
    report=scan_sql_tree(tmp_path)
    assert not report['readyForConversion']
    assert 'existing_destination_collision' in {i['code'] for i in report['issues']}


def test_wrong_collection_prefix_is_rejected(tmp_path):
    add(tmp_path,'seeds/audio/V001__artist.sql')
    report=scan_sql_tree(tmp_path)
    assert not report['readyForConversion']
    assert 'prefix_collection_mismatch' in {i['code'] for i in report['issues']}


def test_aliases_are_never_merged(tmp_path):
    add(tmp_path,'authentication_config/V001__configuration.sql')
    add(tmp_path,'seeds/authentication-config/S001__settings.sql')
    report=scan_sql_tree(tmp_path)
    assert len(report['databaseGroups'])==2
    assert any(w['code']=='possible_database_group_alias' for w in report['warnings'])


def test_scan_is_repeatable_and_never_writes(tmp_path):
    p=add(tmp_path,'audio/V001__artist.sql')
    before=list(tmp_path.rglob('*'))
    result=scan_sql_tree(tmp_path)
    assert result==scan_sql_tree(tmp_path)
    assert before==list(tmp_path.rglob('*'))
    assert p.read_text()=='SELECT 1;'


def test_outside_sql_root_refused(tmp_path):
    (tmp_path/'sql').mkdir()
    with pytest.raises(ValueError): scan_sql_tree(tmp_path,sql_root='..')


def test_missing_sql_directory_refused(tmp_path):
    with pytest.raises(ValueError): scan_sql_tree(tmp_path)


def test_bad_unrecognized_sql_name_fails_closed(tmp_path):
    add(tmp_path,'media/updates.sql')
    report=scan_sql_tree(tmp_path)
    assert not report['readyForConversion']
    assert report['files'][0]['proposedName'] is None


def test_nested_crispy_groups_have_independent_identities(tmp_path):
    add(tmp_path, 'crispy-database/migrations/audio/V001__artist.sql')
    add(tmp_path, 'crispy-database/migrations/cinema/V001__genres.sql')
    add(tmp_path, 'crispy-database/migrations/authentication_config/V001__schema_migrations.sql')
    report = scan_sql_tree(tmp_path)
    assert report['readyForConversion']
    assert set(report['databaseGroups']) == {'audio', 'cinema', 'authentication_config'}
    assert not any(issue['code'] == 'duplicate_version_order' for issue in report['issues'])
    mappings = {(m['databaseGroup'], m['collection']): m['candidateActivePaths'] for m in report['configMappingCandidates']}
    assert mappings[('audio', 'migrations')] == ['sql/crispy-database/migrations/audio']


def test_loose_sql_files_are_not_database_groups(tmp_path):
    add(tmp_path, 'users-db-structure.sql')
    add(tmp_path, 'master_media.sql')
    report = scan_sql_tree(tmp_path)
    assert report['databaseGroups'] == []
    assert all(f['databaseGroup'] is None for f in report['files'])
    assert not report['readyForConversion']


def test_seed_alias_is_visible_but_not_automatically_joined(tmp_path):
    add(tmp_path, 'crispy-database/migrations/authentication_config/V001__schema_migrations.sql')
    add(tmp_path, 'seeds/authentication-config/application_settings.sql')
    report = scan_sql_tree(tmp_path)
    assert set(report['databaseGroups']) == {'authentication_config', 'authentication-config'}
    assert any(w['code'] == 'possible_database_group_alias' for w in report['warnings'])


def test_nested_subdirectory_maps_to_single_active_root(tmp_path):
    add(tmp_path, 'crispy-database/migrations/audio/part1/V001__artist.sql')
    add(tmp_path, 'crispy-database/migrations/audio/part2/V002__album.sql')
    report = scan_sql_tree(tmp_path)
    assert not any(i['code'] == 'multiple_active_directories' for i in report['issues'])
    mapping = report['configMappingCandidates'][0]
    assert mapping['candidateActivePaths'] == ['sql/crispy-database/migrations/audio']
