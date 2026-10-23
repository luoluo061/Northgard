"""Create minimal runtime rules from the user's verified local installation.

No downloads, process access or game writes. Generated definitions stay ignored.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'research'))
from inspect_northgard_assets import read_pak_index, read_entry


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def steam_candidates():
    """Read registered Steam libraries; never recursively search user's drives."""
    roots = set()
    if sys.platform == 'win32':
        import winreg
        for hive, key, value in [(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam', 'SteamPath'),
                                 (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Valve\Steam', 'InstallPath')]:
            try:
                with winreg.OpenKey(hive, key) as handle:
                    roots.add(Path(winreg.QueryValueEx(handle, value)[0]))
            except OSError:
                pass
    for name in ('ProgramFiles(x86)', 'ProgramFiles'):
        if os.environ.get(name):
            roots.add(Path(os.environ[name]) / 'Steam')
    libraries = set(roots)
    for root in roots:
        file = root / 'steamapps/libraryfolders.vdf'
        if file.is_file():
            for match in re.finditer(r'"path"\s*"([^"\r\n]+)"', file.read_text(encoding='utf-8-sig')):
                libraries.add(Path(match.group(1).replace('\\\\', '\\')))
    results = []
    for root in libraries:
        manifest = root / 'steamapps/appmanifest_466560.acf'
        if not manifest.is_file():
            continue
        match = re.search(r'"installdir"\s*"([^"\r\n]+)"', manifest.read_text(encoding='utf-8-sig'))
        if match:
            relative = Path(match.group(1))
            if relative.is_absolute() or '..' in relative.parts:
                continue
            candidate = root / 'steamapps/common' / relative
            if (candidate / 'hlboot.dat').is_file() and (candidate / 'res.client.pak').is_file():
                results.append(candidate.resolve())
    return sorted(set(results), key=str)


def choose_installation(explicit=None):
    if explicit is not None:
        return Path(explicit).resolve()
    saved = ROOT / '.local/installation.json'
    if saved.is_file():
        path = Path(json.loads(saved.read_text(encoding='utf-8'))['game_directory'])
        if (path / 'hlboot.dat').is_file():
            return path.resolve()
    candidates = steam_candidates()
    if len(candidates) != 1:
        raise ValueError('Could not select one Steam installation. Pass --game-dir with the folder containing Northgard.exe.')
    return candidates[0]


def pick(value, schema):
    """Project numeric rule inputs without text, art, paths or asset references."""
    if schema is True:
        return value if value is None or type(value) in (str, int, float, bool) else None
    if isinstance(schema, list):
        return [pick(item, schema[0]) for item in value] if type(value) is list else []
    if type(value) is not dict:
        return {}
    return {key: pick(value[key], child) for key, child in schema.items() if key in value}


VALUE = {'id': True, 'val': True, 'type': True, 'props': {'type': True},
         'upgradedVals': [{'val': True}]}
REQ = {'buildings': [{'building': True}], 'techs': [{'tech': True}],
       'clans': {'type': True, 'clans': [{'clan': True}]},
       'canOutsideTerritory': True, 'onlyBifrost': True}
TABLE_FIELDS = {
    'unit': {'id': True, 'flags': True, 'types': True,
             'props': {'upgradeBonus': True, 'clanType': True, 'canBefriendTech': True},
             'produce': [{'r': True, 'q': True, 'props': {'replaceBy': True}, 'req': REQ}]},
    'resource': {'id': True, 'isProductible': True, 'props': {'converts': True}},
    'building': {'id': True, 'flags': True, 'values': [VALUE],
                 'props': {'localBonus': True, 'localProductionBonus': True, 'canProduce': True}},
    'tech': {'id': True, 'values': [VALUE]},
    'cst': {'id': True, 'value': True},
    'clan': {'id': True, 'bonuses': [{'values': [{'id': True, 'val': True}]}]},
}


def derive_rules(database, profile):
    sheets = {s['name']: s for s in database['sheets']}
    unit_rows = sheets['unit']['lines']
    resource_rows = sheets['resource']['lines']
    minimal_unit = {'schema_version': 1, 'purpose': 'minimal_collector_runtime_definition', 'rows': [
        {'id': row['id'], 'types': row.get('types', 0),
         'props': {'canBefriendTech': bool(row.get('props', {}).get('canBefriendTech'))}}
        for row in unit_rows]}
    minimal_resource = {'schema_version': 1, 'purpose': 'minimal_collector_runtime_definition', 'rows': [
        {'id': row['id'], '_row_index': i, 'isProductible': row.get('isProductible'),
         'props': {'converts': row.get('props', {}).get('converts')}}
        for i, row in enumerate(resource_rows)]}
    labor = {'schema_version': 1, 'source': 'local_verified_game_minimal_rules',
             'hlboot_sha256': profile['hlboot_sha256'], 'data_cdb_sha256': profile['data_cdb_sha256'],
             'tables': {name: [pick(row, fields) for row in sheets.get(name, {}).get('lines', [])]
                        for name, fields in TABLE_FIELDS.items()}}
    return {'unit.json': minimal_unit, 'resource.json': minimal_resource, 'labor_rules.json': labor}


def setup(game_directory, root=ROOT):
    root = Path(root).resolve()
    game_directory = Path(game_directory).resolve()
    profile = json.loads((root / 'research/runtime/build_profile.json').read_text(encoding='utf-8'))
    if sha256_file(game_directory / 'hlboot.dat') != profile['hlboot_sha256']:
        raise ValueError('Unsupported game bytecode. Collection stays disabled; do not replace the verified hash.')
    pak = game_directory / 'res.client.pak'
    entry = next((e for e in read_pak_index(pak)['entries'] if e['path'] == 'data.cdb'), None)
    if entry is None:
        raise ValueError('The installation has no data.cdb entry.')
    raw = read_entry(pak, entry)
    if hashlib.sha256(raw).hexdigest() != profile['data_cdb_sha256']:
        raise ValueError('Unsupported game rules. Collection stays disabled; do not replace the verified hash.')
    rules = derive_rules(json.loads(raw.decode('utf-8-sig')), profile)
    target = root / 'research/static'
    target.mkdir(parents=True, exist_ok=True)
    for name, value in rules.items():
        path = target / name
        temporary = path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
        temporary.replace(path)
    local = root / '.local'
    local.mkdir(exist_ok=True)
    (local/'rules-manifest.json').write_text(json.dumps({'schema_version':1,
        'hlboot_sha256':profile['hlboot_sha256'],'data_cdb_sha256':profile['data_cdb_sha256'],
        'files':{name:sha256_file(target/name) for name in rules}},indent=2),encoding='utf-8')
    (local / 'installation.json').write_text(json.dumps({'game_directory': str(game_directory),
        'game_build': profile['game_build']}, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'status': 'ready_for_local_test', 'game_build': profile['game_build'],
            'verified_bytecode': True, 'verified_rules': True,
            'generated_files': [f'research/static/{name}' for name in rules],
            'game_files_modified': False, 'network_used': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-dir', type=Path)
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.error('Python 3.11 or newer is required.')
    try:
        print(json.dumps(setup(choose_installation(args.game_dir)), ensure_ascii=False))
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
        parser.exit(2, str(error)+'\n')


if __name__ == '__main__':
    main()
