"""Inspect a minimal local definition index, not a live map or reward ledger."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'research'))
from setup_local import choose_installation, sha256_file
from inspect_northgard_assets import read_pak_index, read_entry
from companion.core.known_zone_details import HLBOOT_SHA256, DATA_CDB_SHA256, KINDS, CREATURES

TOKEN = re.compile(r'[A-Za-z][A-Za-z0-9_.]{0,127}\Z')
IMPLEMENTED_UNITS = CREATURES
IMPLEMENTED_BUILDINGS = frozenset(KINDS)
REFERENCE = {'res': True, 'qty': True, 'reqAbility': True, 'reqTech': True,
             'onlyForClan': True, 'clan': True}
CLANS = [{'clan': True}]
VALUE_BUILDINGS = frozenset(('RuneStone', 'CircleStone', 'AncientGraveyard', 'Geyser',
    'ThorWrath', 'FountainFame', 'FountainHealth', 'Yggdrasil', 'Helheim', 'CursedTreasure'))
VALUE = {'id': True, 'val': True, 'props': {'res': True, 'type': True,
    'clanType': True, 'anywhere': True, 'notForClans': CLANS}}
UNIT_PROPS = {'clanType': True, 'onlyForClans': CLANS, 'notForClans': CLANS,
              'preCache': [True], 'canBefriend': True, 'canBefriendTech': True,
              'clanWarchief': True, 'refHero': True}
BUILDING_PROPS = {'canProduce': [{'res': True}], 'fixedAssign': True, 'trainTo': True,
    'spawnFoe': True, 'spawnFoes': [{'unit': True, 'foe': True, 'count': True,
                                  'qty': True, 'proba': True, 'weight': True}],
    'spawnType': [{'unitType': True, 'unitCost': [{'resource': True, 'value': True}],
                   'maxCount': True, 'cooldown': True, 'cooldownType': True}],
    'spawnSpeed': True, 'spawnMax': True, 'spawnFirst': True,
    'winRes': [REFERENCE], 'giveFameOnDiscovery': True, 'preCache': [True],
    'onlyForClans': CLANS, 'notForClans': CLANS, 'clanType': True,
    'conquestRule': True, 'valuesFromRule': True, 'valuesFromBuilding': True,
    'bifrostUnlockedFor': CLANS, 'eventRestricted': True, 'blockColonize': True,
    'creatorBased': True, 'givesAbility': True}
ASSIGN = {'flags': True, 'acceptUnits': [{'unit': True}], 'fakeProduce': True,
          'clanFlags': [{'clan': True, 'flags': True}]}
GEN = {'flags': True, 'probaAppear': True, 'spreadCount': True, 'playerDist': True,
       'createdBy': True}
NEUTRAL_PROPS = {'giftUnit': True, 'singleBefriend': True, 'valueTrade': True,
    'favoriteRes': [{'res': True, 'bonus': True}], 'stocks': [{'res': True, 'val': True}],
    'purchasableUnits': [{'unit': True, 'max': True, 'cost': True}]}


def _project(value, schema):
    """Presence-preserving field projection; never copies asset/text branches."""
    if value is None:
        return None
    if schema is True:
        if type(value) in (str, bool, int):
            return value
        if type(value) is float and math.isfinite(value):
            return value
        raise ValueError('Unexpected non-scalar definition input')
    if type(schema) is list:
        if type(value) is not list or len(value) > 4096:
            raise ValueError('Unexpected definition list')
        return [_project(item, schema[0]) for item in value]
    if type(value) is not dict:
        raise ValueError('Unexpected definition object')
    return {key: _project(value[key], child) for key, child in schema.items() if key in value}


def _groups(sheet):
    # Empty groups can share the next row ID. Enumerate BEFORE taking the last
    # boundary for that ID, otherwise compiled CDB group numbers shift.
    separators = sheet.get('separators', [])
    headings = [s for s in separators if type(s) is dict and s.get('title')]
    groups = [{'index': i, 'title': s['title'], 'first_id': s['id']}
              for i, s in enumerate(headings)]
    boundaries = {s['id']: (i, s['title']) for i, s in enumerate(headings)}
    return groups, boundaries


def _rows(sheet):
    rows = sheet.get('lines')
    if type(rows) is not list or len(rows) > 10000:
        raise ValueError('Invalid definition table')
    seen = set()
    for row in rows:
        if (type(row) is not dict or type(row.get('id')) is not str
                or not TOKEN.fullmatch(row['id']) or row['id'] in seen):
            raise ValueError('Invalid or duplicate definition identifier')
        seen.add(row['id'])
    return rows


def _status(kind, implemented):
    return 'implemented_not_live_validated' if kind in implemented else 'catalog_only'


def _configured_values(raw_values):
    """Keep static references and override presence, never evaluate expressions."""
    values = _project(raw_values, [VALUE])
    if values is None:
        return None
    for raw, value in zip(raw_values, values):
        if type(raw) is not dict:
            raise ValueError('Unexpected configured value')
        value['has_dynamic_definition'] = bool(raw.get('dyn'))
        value['has_upgrade_overrides'] = bool(raw.get('upgradedVals'))
    return values


def build_catalog(database, profile):
    """Pure minimal projection. Matching profile labels do not authenticate fixtures."""
    if (type(profile) is not dict or profile.get('hlboot_sha256') != HLBOOT_SHA256
            or profile.get('data_cdb_sha256') != DATA_CDB_SHA256):
        raise ValueError('Unsupported game profile; no catalog generated')
    if type(database) is not dict or type(database.get('sheets')) is not list:
        raise ValueError('Missing game definition sheets')
    tables = {s['name']: s for s in database['sheets'] if type(s) is dict and 'name' in s}
    if any(name not in tables for name in ('unit', 'building', 'neutral')):
        raise ValueError('Required definition table absent')
    output = {'schema_version': 1, 'artifact_type': 'northgard_static_map_definition_catalog',
        'source': 'local_verified_installed_configuration', 'local_only': True,
        'data_semantics': 'configured_not_actual', 'mode_eligibility': 'not_evaluated',
        'projection_semantics': 'selected_configuration_fields_only',
        'runtime_scope_expanded': False, 'is_live_map': False,
        'is_capture_or_submission': False, 'reward_conditions_complete': False,
        'hlboot_sha256': HLBOOT_SHA256, 'data_cdb_sha256': DATA_CDB_SHA256,
        'unit_groups': [], 'building_groups': [], 'units': [], 'buildings': [], 'neutrals': []}
    output['unit_groups'], unit_boundaries = _groups(tables['unit'])
    output['building_groups'], building_boundaries = _groups(tables['building'])
    for name, boundaries, implemented, target in (
            ('unit', unit_boundaries, IMPLEMENTED_UNITS, 'units'),
            ('building', building_boundaries, IMPLEMENTED_BUILDINGS, 'buildings')):
        current = (None, None)
        for row in _rows(tables[name]):
            current = boundaries.get(row['id'], current)
            entry = {'id': row['id'], 'group_index': current[0], 'group_title': current[1],
                     'runtime_support': _status(row['id'], implemented),
                     'mode_eligibility': 'not_evaluated'}
            entry.update(_project(row, {'flags': True, 'types': True} if name == 'unit'
                                  else {'flags': True, 'assign': ASSIGN, 'gen': GEN}))
            if 'props' in row:
                entry['props'] = _project(row['props'], UNIT_PROPS if name == 'unit' else BUILDING_PROPS)
            if name == 'building' and row['id'] in VALUE_BUILDINGS and 'values' in row:
                entry['values'] = _configured_values(row['values'])
                entry['values_semantics'] = 'configured_reference_not_effective'
            if name == 'unit':
                entry['neutral_candidate_hint'] = current[0] in range(35, 41)
                entry['candidate_hint_semantics'] = 'compiled_group_35_to_40_not_runtime_whitelist'
                phases = row.get('phases', [])
                entry['configured_phase_loot'] = [
                    {'phase_index': i, 'loot': _project(p['lootTable'], [REFERENCE])}
                    for i, p in enumerate(phases) if 'lootTable' in p]
            output[target].append(entry)
    for row in _rows(tables['neutral']):
        entry = {'id': row['id'], 'runtime_support': 'catalog_only',
                 'mode_eligibility': 'not_evaluated'}
        entry.update(_project(row, {'unit': True, 'place': True,
            'otherBuildings': [{'building': True}], 'befriendGift': [REFERENCE],
            'eradicationReward': [REFERENCE], 'alignmentSteps': [{'step': True}],
            'props': NEUTRAL_PROPS}))
        output['neutrals'].append(entry)
    output['summary'] = summary(output)
    return output


def summary(catalog):
    return {'unit_rows': len(catalog['units']), 'building_rows': len(catalog['buildings']),
        'neutral_rows': len(catalog['neutrals']),
        'neutral_candidate_hint_rows': sum(r['neutral_candidate_hint'] for r in catalog['units']),
        'runtime_support_counts': dict(Counter(r['runtime_support']
            for name in ('units', 'buildings', 'neutrals') for r in catalog[name])),
        'building_fame_reference_rows': sum(any(x.get('res') == 'Fame'
            for key in ('winRes',) for x in r.get('props', {}).get(key, []))
            or 'giveFameOnDiscovery' in r.get('props', {}) for r in catalog['buildings'])}


def search_catalog(catalog, query):
    if type(query) is not str or not query.strip():
        raise ValueError('Search requires a nonempty ID substring')
    needle = query.casefold()
    return {name: [row for row in catalog[name] if needle in row['id'].casefold()]
            for name in ('units', 'buildings', 'neutrals')}


def load_installed_catalog(game_dir=None):
    profile = json.loads((ROOT / 'research/runtime/build_profile.json').read_text(encoding='utf-8'))
    installation = choose_installation(game_dir).resolve()
    if sha256_file(installation / 'hlboot.dat') != profile['hlboot_sha256']:
        raise ValueError('Unsupported installed bytecode; no catalog generated')
    pak = installation / 'res.client.pak'
    entries = [e for e in read_pak_index(pak)['entries'] if e['path'] == 'data.cdb']
    if len(entries) != 1:
        raise ValueError('Expected exactly one installed data.cdb')
    raw = read_entry(pak, entries[0])
    if hashlib.sha256(raw).hexdigest() != profile['data_cdb_sha256']:
        raise ValueError('Unsupported installed rules; no catalog generated')
    return build_catalog(json.loads(raw.decode('utf-8-sig')), profile), installation


def write_catalog(catalog, output, *, installation):
    destination = Path(output).resolve()
    if destination.is_relative_to(Path(installation).resolve()):
        raise ValueError('Catalog output must be outside the game installation')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('x', encoding='utf-8') as stream:
        json.dump(catalog, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-dir', type=Path)
    parser.add_argument('--summary', action='store_true')
    parser.add_argument('--search', help='Case-insensitive definition ID substring')
    parser.add_argument('--out', type=Path, help='New local output file; existing files are not overwritten')
    args = parser.parse_args(argv)
    try:
        catalog, installation = load_installed_catalog(args.game_dir)
        if args.out:
            write_catalog(catalog, args.out, installation=installation)
        result = {'artifact_type': catalog['artifact_type'], 'local_only': True,
            'data_semantics': 'configured_not_actual', 'mode_eligibility': 'not_evaluated',
            'runtime_scope_expanded': False, 'output_written': args.out is not None,
            'summary': catalog['summary']}
        if args.search:
            result['matches'] = search_catalog(catalog, args.search)
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(2, f'Catalog unavailable: {type(error).__name__}; no game changes or upload performed.\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
