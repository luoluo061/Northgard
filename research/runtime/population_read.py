"""Read-only population counter derived from this installed build's bytecode.

Supports the standard Bear/Raven single-player validation cases. It reads only
the player's clan and Units subtree, never player names or account identifiers.
Unsupported special-mode branches fail explicitly rather than invent a count.
Evidence: population_algorithm.json, unit_bytecode.json, static/unit.json.
"""
from __future__ import annotations

import json
from pathlib import Path

_UNIT_DEFINITIONS = None
_TWO_POP = {'Bearserker', 'Boarserker', 'Foxserker', 'PackBrawler', 'Wolfserker'}
_ZERO_POP = {'Bodyguard', 'Bodyguard02', 'Caravan', 'Catapult', 'Gayant',
             'Giant', 'GiantBoar', 'GiantCrow', 'Hawk', 'Mercenary',
             'UndeadGiant', 'WhiteWolf'}
_RAISED = {'DarkDwarf', 'DarkDwarfChampion', 'DarkDwarfKing'}


def _definitions():
    global _UNIT_DEFINITIONS
    if _UNIT_DEFINITIONS is None:
        source = Path(__file__).resolve().parents[1] / 'static' / 'unit.json'
        data = json.loads(source.read_text(encoding='utf8'))
        _UNIT_DEFINITIONS = {row['id']: row for row in data['rows']}
    return _UNIT_DEFINITIONS


def _string(h, address):
    if not address:
        return ''
    if h.object_type(address).name != 'String':
        raise ValueError('Expected Haxe String')
    length = h.field(address, 'length')
    if not 0 <= length <= 256:
        raise ValueError('Invalid kind string length')
    return h.read(h.field(address, 'bytes'), length * 2).decode('utf-16-le')


def unit_population(kind, runtime_flags, definition, *, has_inf=True,
                    runtime_type='ent.Unit', neutral_owner=False):
    """Return (weight, reason), matching normal Bear/Raven bytecode branches."""
    if not has_inf:
        return 0, 'no_unit_definition'
    types = definition.get('types', 0)
    # computePop exclusions before virtual getPopulationCount invocation.
    if types & (1 << 15):
        return 0, 'soul_ghost'
    if kind in _RAISED:
        return 0, 'raised'
    if runtime_flags & (1 << 29):
        return 0, 'runtime_exclusion_flag'
    if runtime_type == 'ent.e.LeggedOwlBranch':
        return 0, 'overridden_population_count'
    if kind in {'PeasantMilitia', 'OwlNorn'}:
        raise ValueError('Special clan/bonus population branch needs verification')
    # ent.Unit.getPopulationCount, function 4313.
    if kind in _TWO_POP:
        return 2, 'two_population_unit'
    if kind in _ZERO_POP:
        return 0, 'population_exempt_kind'
    if types & (1 << 13) or kind == 'StoatHero':
        return 0, 'lord_or_stoat_hero'
    if types & ((1 << 4) | (1 << 5) | (1 << 8)):
        return 0, 'hero_warchief_or_companion'
    if kind == 'Slave':
        return 0, 'slave_separate_counter'
    if runtime_flags & 4096:
        return 0, 'runtime_population_exempt_flag'
    if kind == 'OxShadow':
        return 0, 'bodyguard'
    if kind in {'SpectralWolf', 'SpectralBear', 'SpectralRam'}:
        raise ValueError('Spectral WildCall affect needs verification')
    if neutral_owner:
        raise ValueError('Neutral faction befriending state needs verification')
    props = definition.get('props', {})
    if props.get('canBefriendTech'):
        raise ValueError('Befriending technology population branch needs verification')
    return 1, 'ordinary_population_unit'


def read_population(h, player_address):
    """Return population, unit_count and a kind/weight breakdown from live data.

    ``h`` is runtime_types.HLReader. No game/native function is invoked.
    The unit array shape is checked again after reading to catch resizing.
    """
    clan = _string(h, h.field(player_address, 'clan'))
    if clan not in {'Bear', 'Raven'}:
        raise ValueError(f'Population validation currently supports Bear/Raven, got {clan}')
    wrapper = h.field(player_address, 'units')
    length = h.field(wrapper, 'length')
    storage = h.field(wrapper, 'array')
    if not 0 <= length <= 10000:
        raise ValueError('Invalid unit count')
    native = h.native_array(storage, limit=20000)
    if length > native['size']:
        raise ValueError('Unit array length exceeds native capacity')
    definitions = _definitions()
    breakdown = []
    for address in native['values'][:length]:
        if not address:
            raise ValueError('Null unit in active player unit array')
        unit_type = h.object_type(address).name
        kind = _string(h, h.field(address, 'kind'))
        definition = definitions.get(kind)
        if definition is None:
            raise ValueError(f'Unknown unit kind: {kind}')
        flags_object = h.field(address, 'flags')
        bits = h.field(flags_object, 'value') if flags_object else 0
        has_inf = bool(h.field(address, 'inf'))
        neutral_owner = bool(h.field(address, 'neutralOwner'))
        weight, reason = unit_population(kind, bits, definition, has_inf=has_inf,
                                         runtime_type=unit_type, neutral_owner=neutral_owner)
        breakdown.append({'kind': kind, 'weight': weight, 'reason': reason})
    if h.field(wrapper, 'length') != length or h.field(wrapper, 'array') != storage:
        raise ValueError('Unit array changed while sampling; retry')
    return {'population': sum(row['weight'] for row in breakdown),
            'unit_count': length, 'breakdown': breakdown,
            'method': 'bytecode_population_rules',
            'scope': 'standard Bear/Raven units in installed build'}
