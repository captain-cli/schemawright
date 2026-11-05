"""Phase 7A: mock-only tests; no live DB or secret values printed."""
import json
import os
from pathlib import Path

import pytest

from schemawright import deployment_preflight as mod


def fixture(tmp_path):
    p = tmp_path / 'databases.json'
    p.write_text(json.dumps({'appdb': {'driver': 'mysql', 'database': 'schemawright_test',
                                      'host': '127.0.0.1', 'port': 3306,
                                      'passwordRef': 'env:APP_DB_PASSWORD'}}))
    secret = tmp_path / 'secrets.env'
    secret.write_text('APP_DB_PASSWORD=TOP_SECRET_VALUE\n')
    os.chmod(secret, 0o600)
    args = dict(source=p, project_root=tmp_path, database='appdb',
                read_only_user='schemawright_app', secrets_file=secret)
    return args


def success_monkeypatch(monkeypatch, *, pending=0, drift=False):
    calls = []
    def live(db, **kw):
        calls.append(('live', kw['username'], kw['password']))
        return {'safeToPlan': not drift, 'historyTableExists': True,
                'issues': ['drift'] if drift else []}
    def recovery(db, **kw):
        calls.append(('recovery', kw['username'], kw['password']))
        return {'safeToProceed': not drift, 'historyTableExists': True,
                'issues': [], 'counts': {'applied': 4, **({'pending': pending} if pending else {}),
                                         **({'checksum_drift': 1} if drift else {})}}
    monkeypatch.setattr(mod, 'migration_status', live)
    monkeypatch.setattr(mod, 'inspect_recovery', recovery)
    return calls


def test_ready_up_to_date(tmp_path, monkeypatch):
    args = fixture(tmp_path)
    calls = success_monkeypatch(monkeypatch)
    result = mod.deployment_preflight(**args)
    assert result['status'] == 'ready' and result['upToDate']
    assert result['changesExecuted'] is False and not result['executionAuthorized']
    assert result['checks']['credentialwright']['credentialValuesExposed'] is False
    assert len(calls) == 2 and all(c[1:] == ('schemawright_app', 'TOP_SECRET_VALUE') for c in calls)
    assert 'TOP_SECRET_VALUE' not in json.dumps(result)


def test_ready_with_pending(tmp_path, monkeypatch):
    args = fixture(tmp_path)
    success_monkeypatch(monkeypatch, pending=2)
    result = mod.deployment_preflight(**args)
    assert result['readyToDeploy'] and not result['upToDate']
    assert result['migrationCounts']['pending'] == 2


def test_drift_blocks(tmp_path, monkeypatch):
    args = fixture(tmp_path)
    success_monkeypatch(monkeypatch, drift=True)
    result = mod.deployment_preflight(**args)
    assert result['status'] == 'blocked' and result['blockerCodes'] == ['migration_state_blocked']
    assert result['changesExecuted'] is False


def test_unsafe_secret_permissions_refused_before_connection(tmp_path, monkeypatch):
    args = fixture(tmp_path)
    Path(args['secrets_file']).chmod(0o644)
    def never(*a, **kw): raise AssertionError('should not connect')
    monkeypatch.setattr(mod, 'migration_status', never)
    with pytest.raises(mod.DeploymentPreflightError) as e:
        mod.deployment_preflight(**args)
    assert e.value.code == 'secret_file_unsafe'


def test_symlink_secret_refused(tmp_path):
    args = fixture(tmp_path)
    symlink = tmp_path / 'linked.env'
    symlink.symlink_to(args['secrets_file'])
    args['secrets_file'] = symlink
    with pytest.raises(mod.DeploymentPreflightError) as e:
        mod.deployment_preflight(**args)
    assert e.value.code == 'secret_file_unsafe'


def test_missing_credentials_redacted(tmp_path):
    args = fixture(tmp_path)
    Path(args['secrets_file']).write_text('WRONG_KEY=TOP_SECRET_VALUE\n')
    with pytest.raises(mod.DeploymentPreflightError) as e:
        mod.deployment_preflight(**args)
    assert e.value.code == 'credential_unavailable'
    assert 'TOP_SECRET_VALUE' not in json.dumps(mod.error_report(e.value))


def test_reject_migrator_inspection(tmp_path):
    args = fixture(tmp_path)
    args['read_only_user'] = 'schemawright_migrator'
    with pytest.raises(mod.DeploymentPreflightError) as e:
        mod.deployment_preflight(**args)
    assert e.value.code == 'invalid_inspection_identity'


def test_inspection_error_is_redacted(tmp_path, monkeypatch):
    args = fixture(tmp_path)
    def broken(*a, **kw): raise RuntimeError('TOP_SECRET_VALUE private driver error')
    monkeypatch.setattr(mod, 'migration_status', broken)
    with pytest.raises(mod.DeploymentPreflightError) as e:
        mod.deployment_preflight(**args)
    assert e.value.code == 'database_inspection_failed'
    assert 'TOP_SECRET_VALUE' not in json.dumps(mod.error_report(e.value))


def test_no_database_config(tmp_path):
    args = fixture(tmp_path)
    Path(args['source']).write_text('{}')
    with pytest.raises(mod.DeploymentPreflightError) as e:
        mod.deployment_preflight(**args)
    assert e.value.code == 'invalid_configuration'
