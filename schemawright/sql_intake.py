"""Phase 7B.1: read-only intake of legacy SQL trees for SchemaWright.

Inventory and propose names only. Never renames, copies, writes configuration,
opens databases, or executes SQL. Ambiguous objects have no proposed target.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
from pathlib import Path
import re
from typing import Any

CANONICAL = re.compile(r'^(?P<prefix>[VSRPT])(?P<version>\d{4})__(?P<order>\d{4})__(?P<desc>[a-z0-9_-]+)\.sql$')
LEGACY = re.compile(r'^(?P<prefix>[VSRPT])(?P<number>\d{1,4})__(?P<desc>[a-z0-9_-]+)\.sql$', re.I)
COLLECTIONS = {'migrations': 'V', 'seeds': 'S', 'procedures': 'P', 'triggers': 'T', 'rollbacks': 'R'}
ARCHIVES = {'archive', 'archives', 'archived'}


def _safe_relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _classification(parts: tuple[str, ...]) -> tuple[str | None, str | None, str, list[str]]:
    """Return an *unverified* logical group, collection, stage and ambiguity notes.

    A container such as ``crispy-database/migrations`` isn't itself a DB:
    its next directory names the logical migration group. No physical schema
    mapping is inferred from folder names.
    """
    if not parts:
        return None, None, 'unknown', ['sql_root_file']
    tokens = [part.lower() for part in parts]
    stage = 'archive' if any(token in ARCHIVES for token in tokens[:-1]) else 'active'
    if len(parts) == 1:
        return None, None, stage, ['sql_root_file']
    if tokens[0] in COLLECTIONS:
        kind = tokens[0]
        if len(parts) < 3 or tokens[1] in ARCHIVES:
            return None, kind, stage, ['database_group_unresolved'] if stage != 'archive' else []
        return parts[1], kind, stage, []
    if len(parts) > 2 and tokens[1] in COLLECTIONS:
        kind = tokens[1]
        # sql/<container>/<collection>/<group>/<file>.sql
        if len(parts) >= 4 and tokens[2] not in ARCHIVES:
            return parts[2], kind, stage, []
        # sql/<db>/<collection>/<file>.sql is also a valid compact layout.
        if len(parts) == 3 and tokens[2] not in ARCHIVES:
            return parts[0], kind, stage, []
        return None, kind, stage, ['database_group_unresolved'] if stage != 'archive' else []
    # sql/<db>/<file>.sql (a direct per-database layout).
    if len(parts) == 2:
        return parts[0], None, stage, []
    return None, None, stage, ['collection_unresolved']


def _candidate_root(parts: tuple[str, ...], kind: str) -> str:
    """Return one configured active root, not each nested file's parent."""
    tokens = [part.lower() for part in parts]
    if tokens[0] in COLLECTIONS:
        return Path(*parts[:2]).as_posix()
    if len(parts) >= 4 and tokens[1] in COLLECTIONS:
        return Path(*parts[:3]).as_posix()
    if len(parts) >= 3 and tokens[1] in COLLECTIONS:
        return Path(*parts[:2]).as_posix()
    return parts[0]


def scan_sql_tree(project_root: str | Path, *, sql_root: str | Path = 'sql') -> dict[str, Any]:
    """Scan an existing SQL package without changing its files or accessing a DB.

    The first path component ordinarily identifies the logical DB. Top-level
    collection directories (sql/seeds/<db>/...) are also recognized.
    Legacy VNNN__name.sql is proposed as V0001__NNN*10__name.sql, preserving
    observed order while recording that a baseline version is an assumption.
    Seeds without explicit order are never assigned an arbitrary position.
    """
    root = Path(project_root).expanduser().resolve()
    requested = Path(sql_root).expanduser()
    base = requested if requested.is_absolute() else root / requested
    base = base.resolve()
    if not base.is_relative_to(root):
        raise ValueError('SQL root must be contained within the project root')
    if not base.is_dir():
        raise ValueError('SQL root directory does not exist')
    issues: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    files: list[dict[str, Any]] = []
    proposed_names: dict[str, str] = {}
    identities: dict[tuple[str, str, int, int], str] = {}
    group_dirs: dict[tuple[str, str], set[str]] = defaultdict(set)

    for path in sorted(base.rglob('*')):
        if path.suffix.lower() != '.sql':
            continue
        relative = _safe_relative(path, base)
        parts = tuple(path.relative_to(base).parts)
        database, kind, stage, notes = _classification(parts)
        entry: dict[str, Any] = {
            'source': relative, 'databaseGroup': database, 'collection': kind,
            'stage': stage, 'currentName': path.name, 'proposedName': None,
            'status': 'unresolved', 'sha256': None,
        }
        files.append(entry)
        if path.is_symlink() or not path.is_file():
            issues.append({'code': 'unsafe_sql_path', 'file': relative})
            continue
        # A user-controlled tree may contain symlinked parent directories.
        if not path.resolve().is_relative_to(base):
            issues.append({'code': 'unsafe_sql_path', 'file': relative})
            continue
        try:
            entry['sha256'] = _sha256(path)
        except OSError:
            issues.append({'code': 'unreadable_sql_file', 'file': relative})
            continue
        if notes:
            for note in notes:
                issues.append({'code': note, 'file': relative})
        existing = CANONICAL.fullmatch(path.name)
        legacy = LEGACY.fullmatch(path.name)
        prefix = existing.group('prefix') if existing else legacy.group('prefix').upper() if legacy else None
        if kind is None and prefix is not None:
            # Direct db folder: identify by supported explicit file prefix.
            kind = next((n for n, p in COLLECTIONS.items() if p == prefix), None)
            entry['collection'] = kind
        elif kind is not None and prefix is not None and COLLECTIONS[kind] != prefix:
            issues.append({'code': 'prefix_collection_mismatch', 'file': relative})
            continue
        if kind is None:
            issues.append({'code': 'collection_unresolved', 'file': relative})
            continue
        if stage == 'archive':
            entry['status'] = 'archived'
            continue
        if database is None:
            continue
        if existing:
            version, order = int(existing['version']), int(existing['order'])
            entry['status'] = 'compliant'
            entry['proposedName'] = path.name
        elif legacy:
            original_sequence = int(legacy['number'])
            order = original_sequence * 10
            if not 1 <= order <= 9999:
                issues.append({'code': 'legacy_order_out_of_range', 'file': relative})
                continue
            version = 1
            entry['proposedName'] = f'{prefix}{version:04d}__{order:04d}__{legacy["desc"].lower()}.sql'
            entry['status'] = 'rename-proposed'
            warnings.append({'code': 'assumed_initial_baseline_version', 'file': relative})
        else:
            issues.append({'code': 'unrecognized_filename_manual_order_required', 'file': relative})
            continue
        key = (database.casefold(), kind, version, order)
        if key in identities:
            issues.append({'code': 'duplicate_version_order', 'file': relative,
                           'conflictsWith': identities[key]})
        else:
            identities[key] = relative
        proposed_path = path.with_name(entry['proposedName'])
        dest = _safe_relative(proposed_path, base)
        if dest in proposed_names and proposed_names[dest] != relative:
            issues.append({'code': 'proposed_destination_collision', 'file': relative,
                           'conflictsWith': proposed_names[dest]})
        proposed_names[dest] = relative
        if proposed_path != path and proposed_path.exists():
            issues.append({'code': 'existing_destination_collision', 'file': relative})
        group_dirs[(database, kind)].add((base / _candidate_root(parts, kind)).relative_to(root).as_posix())

    groups = sorted({e['databaseGroup'] for e in files if e['databaseGroup'] is not None and e['collection'] is not None})
    # Multiple candidate active roots cannot be encoded as one PathSet.active.
    mappings = []
    for (database, kind), dirs in sorted(group_dirs.items()):
        if len(dirs) > 1:
            issues.append({'code': 'multiple_active_directories', 'databaseGroup': database,
                           'collection': kind})
        mappings.append({'databaseGroup': database, 'collection': kind,
                         'candidateActivePaths': sorted(dirs),
                         'requiresMappingReview': len(dirs) != 1})
    # Resolve only obvious name variants as a warning; never join aliases automatically.
    alias_map: dict[str, set[str]] = defaultdict(set)
    for name in groups:
        alias_map[re.sub(r'[-_]', '', name).casefold()].add(name)
    for names in alias_map.values():
        if len(names) > 1:
            warnings.append({'code': 'possible_database_group_alias',
                             'groups': ','.join(sorted(names))})
    counts = dict(sorted(Counter(e['status'] for e in files).items()))
    return {
        'contractVersion': '1.0', 'action': 'sql-intake-preview',
        'projectRoot': str(root), 'sqlRoot': str(base),
        'readOnly': True, 'changesExecuted': False, 'executionAuthorized': False,
        'readyForConversion': not issues,
        'databaseGroups': groups, 'counts': counts, 'files': files,
        'configMappingCandidates': mappings, 'issues': issues, 'warnings': warnings,
        'scope': 'offline-sql-inventory-only',
    }
