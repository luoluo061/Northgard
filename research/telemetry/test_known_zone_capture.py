"""Invented heaps exercise the actual background adapter, without a game."""
from copy import deepcopy
from types import SimpleNamespace
import struct
import unittest
from unittest.mock import patch

from research.telemetry import known_zone_capture as module
import match_context
from known_zone_details import validate_known_zone_details

PROFILE = {'hlboot_sha256': module.HLBOOT_SHA256, 'data_cdb_sha256': module.DATA_CDB_SHA256}
IDENTITY = {'session_key': 'test-session', 'capture_run_id': 'test-run', 'capture_segment_id': 'test-run:1'}
CONTEXT = {'mode': {'kind': 'Singleplayer', 'args': [{'kind': 'FreeForAll', 'args': []}]},
           'game_type': {'kind': 'Standard', 'args': []},
           'editor_present': False, 'bifrost_data_present': False}


class Heap:
    def __init__(self):
        self.reads = []
        self.counts = {}
        self.changes = {}
        self.types = {'me': 'ent.Player', 'state': 'GameState', 'game': 'Game',
                      'world': 'ent.player.WorldComponent', 'z1': 'ent.Zone',
                      'z2': 'ent.Zone', 'hidden': 'ent.Zone',
                      'stone_state': module.STATE_TYPE,
                      'mode': 'enum:PlayMode', 'variant': 'enum:MultiMode',
                      'game_type': 'enum:GameType'}
        self.parents = {'ent.Building': 'ent.BaseUnit', 'ent.b.LoreStone': 'ent.Building',
                        'ent.b.CircleStone': 'ent.Building', 'ent.b.Shoal': 'ent.Building',
                        'ent.Unit': 'ent.BaseUnit', 'ent.Foe': 'ent.Unit',
                        'ent.CustomFoe': 'ent.Foe'}
        self.fields = {('state', 'me'): 'me', ('state', 'game'): 'game',
                       ('state', 'players'): 'players', ('me', 'aiJob'): 0,
                       ('me', '__uid'): 100, ('me', 'world'): 'world',
                       ('state', 'time'): 50., ('game', 'state'): 'state',
                       ('game', 'initDone'): True, ('state', 'mode'): 'mode',
                       ('state', 'gameType'): 'game_type', ('state', 'editor'): 0,
                       ('state', 'bifrostData'): 0, ('world', 'discoveredIds'): 'ids',
                       ('state', 'zones'): 'zones', ('z1', 'id'): 1, ('z2', 'id'): 2,
                       ('hidden', 'id'): 3}
        self.arrays = {'players': ['me', 'foreign_player'], 'zones': ['z1', 'z2', 'hidden'],
                       'ids': [1, 2], 'z1b': [], 'z2b': [], 'z1u': [], 'z2u': [],
                       'no_status': []}
        for zone in ('z1', 'z2'):
            self.fields[zone, 'game'] = 'game'
            self.fields[zone, 'buildings'] = zone + 'b'
            self.fields[zone, 'units'] = zone + 'u'
        self.enums = {'mode': ('Singleplayer', [(923, 'variant')]),
                      'variant': ('FreeForAll', []), 'game_type': ('Standard', [])}
        self.boxes = {10001: (6, 30.), 10002: (6, .25),
                      10003: (7, True), 10004: (7, False)}
        self.add_building('stone', 'Stones', 'z1', uid=200)
        self.fields['stone', 'state'] = 'stone_state'
        self.fields['stone_state', 'contains'] = 10001
        self.fields['stone_state', 'progress'] = 10002
        self.add_building('lore', 'RuneStone', 'z2', name='ent.b.LoreStone', uid=201)
        self.add_unit('wolf1', 'Wolf', 'z1', uid=300)
        self.add_unit('wolf2', 'Wolf', 'z1', uid=301)
        self.add_unit('bear', 'Bear', 'z2', uid=302)
        self.add_unit('enemy', 'Warrior', 'z1', uid=303, owner='foreign_player')
        self.add_unit('kaija', 'BearMaiden', 'z1', uid=304, owner='me')

    def add_building(self, pointer, kind, zone, name='ent.Building', uid=200):
        self.types[pointer] = name
        self.fields.update({(pointer, 'zone'): zone, (pointer, 'game'): 'game',
                            (pointer, 'kind'): kind, (pointer, '__uid'): uid,
                            (pointer, 'enabled'): True, (pointer, 'scriptVisible'): 0,
                            (pointer, 'inf'): pointer + '_inf'})
        self.fields[pointer + '_inf', 'id'] = kind
        self.fields[pointer + '_inf', 'flags'] = module.NATURAL_FLAGS.get(kind, 0)
        self.arrays[zone + 'b'].append(pointer)

    def add_unit(self, pointer, kind, zone, uid=300, owner=0, name='ent.Foe'):
        self.types[pointer] = name
        self.fields.update({(pointer, 'owner'): owner, (pointer, 'zone'): zone,
                            (pointer, 'game'): 'game', (pointer, 'kind'): kind,
                            (pointer, '__uid'): uid, (pointer, 'inf'): pointer + '_inf',
                            (pointer, 'enabled'): True, (pointer, 'scriptVisible'): 0,
                            (pointer, 'evars'): 0, (pointer, 'statusList'): 'no_status'})
        self.fields[pointer + '_inf', 'id'] = kind
        self.fields[pointer + '_inf', 'group'] = 36
        self.arrays[zone + 'u'].append(pointer)

    def changed(self, key, original):
        self.counts[key] = self.counts.get(key, 0) + 1
        change = self.changes.get(key)
        return change(self.counts[key], original) if change else original

    def object_type(self, pointer):
        if pointer == 'foreign_player':
            raise AssertionError('Foreign player object must never be dereferenced')
        if pointer in self.boxes:
            return SimpleNamespace(name='boxed', kind=self.boxes[pointer][0], super_type=0)
        name = self.types[pointer]
        return SimpleNamespace(name=name, kind=18 if name.startswith('enum:') else 11,
                               super_type=self.parents.get(name, 0))

    def type_info(self, pointer):
        return SimpleNamespace(name='enum:MultiMode' if pointer == 923 else pointer,
                               super_type=self.parents.get(pointer, 0))

    def field(self, pointer, name):
        if pointer == 'foreign_player' or (pointer in ('enemy', 'kaija') and name != 'owner'):
            raise AssertionError('Player-owned entity details must not be read')
        if pointer == 'hidden' and name != 'id':
            raise AssertionError('Undiscovered zone details must not be read')
        self.reads.append((pointer, name))
        return self.changed(('field', pointer, name), self.fields[pointer, name])

    def int_array(self, pointer):
        return self.changed(('array', pointer), tuple(self.arrays[pointer]))

    def objects(self, pointer, limit):
        return self.changed(('array', pointer), tuple(self.arrays[pointer]))

    def dynamic(self, pointer):
        return self.changed(('box', pointer), self.boxes[pointer][1])


def capture(heap=None, **kwargs):
    h = heap or Heap()
    args = {'known_zone_ids': [1, 2], 'identity': IDENTITY,
            'profile': PROFILE, 'game_context': CONTEXT}
    args.update(kwargs)
    with patch.object(module, '_objects', side_effect=lambda h, p, limit=10000: h.objects(p, limit)), \
            patch.object(module, '_string', side_effect=lambda h, p: p), \
            patch.object(module, '_materialized', side_effect=lambda h, p, n, k: h.field(p, n)), \
            patch.object(match_context, 'enum_values', side_effect=lambda h, p: deepcopy(h.enums[p])):
        return module.capture_known_zone_details(h, 'me', 'state', **args)


class CaptureTests(unittest.TestCase):
    def test_actual_background_paths_and_known_mineral_output(self):
        h = Heap(); row = capture(h)
        stone = row['zones'][0]['resources'][0]
        self.assertEqual(stone['deposits'], [{'natural_object_uid': 200,
            'remaining_contains_raw': 30., 'next_extraction_progress_raw': .25}])
        self.assertEqual(stone['remaining_total_raw'], 30.)
        self.assertEqual(row['zones'][0]['wildlife'], [{'kind': 'Bear', 'count': 0}, {'kind': 'Wolf', 'count': 2}])
        self.assertEqual(row['zones'][1]['resources'][0]['kind'], 'RuneStone')
        self.assertFalse(any(p in ('enemy', 'kaija') and n != 'owner' for p, n in h.reads))
        parent = {'status': 'ok', **IDENTITY, 'game_time_seconds': 50.,
                  'local_player_entity_uid': 100, 'discovered_zone_ids': [1, 2],
                  'game_context': CONTEXT,
                  'local_player_identity': {'verified': True, 'role': 'player', 'is_ai': False,
                                            'in_player_list': True, 'entity_uid': 100}}
        validate_known_zone_details(row, parent, projected=False)

    def test_hidden_zone_and_other_player_payload_never_read(self):
        h = Heap(); capture(h)
        self.assertEqual([n for p, n in h.reads if p == 'hidden'], ['id', 'id'])

    def test_native_multiplayer_cannot_be_enabled_by_caller_label(self):
        h = Heap(); h.enums['mode'] = ('Multiplayer', [(923, 'variant')])
        result = capture(h)
        self.assertEqual(result['status'], 'unsupported_context')
        self.assertEqual(result['zones'], [])
        self.assertFalse(any(n in ('buildings', 'units') for _, n in h.reads))

    def test_native_game_type_and_editor_are_verified(self):
        for change in ('ranked', 'editor', 'bifrost'):
            h = Heap()
            if change == 'ranked': h.enums['game_type'] = ('Ranked', [])
            else: h.fields['state', 'editor' if change == 'editor' else 'bifrostData'] = 999
            self.assertEqual(capture(h)['status'], 'unsupported_context')

    def test_recorded_singleplayer_native_null_game_type_is_supported(self):
        # Independent of the old Standard-only fixture: the pinned native
        # GameState constructors store a null pointer, as in the human match.
        h = Heap(); h.fields['state', 'gameType'] = 0
        context = {**CONTEXT, 'game_type': None}
        row = capture(h, game_context=context)
        self.assertEqual(row['status'], 'partial')
        self.assertEqual(row['zones'][0]['resources'][0]['remaining_total_raw'], 30.)
        self.assertEqual(row['zones'][0]['wildlife_status'], 'ok')
        self.assertIsNone(context['game_type'])
        self.assertNotIn(('game_type', 'kind'), h.reads)

    def test_native_null_requires_explicit_matching_source_context(self):
        for supplied in (dict(CONTEXT), {k: v for k, v in CONTEXT.items() if k != 'game_type'}):
            h = Heap(); h.fields['state', 'gameType'] = 0
            row = capture(h, game_context=supplied)
            self.assertEqual(row['status'], 'unsupported_context')
            self.assertEqual(row['zones'], [])
            self.assertFalse(any(name in ('buildings', 'units') for _, name in h.reads))
        # A null caller label cannot override a non-null native Standard enum.
        self.assertEqual(capture(game_context={**CONTEXT, 'game_type': None})['status'],
                         'unsupported_context')

    def test_null_game_type_never_enables_other_modes_or_damaged_enum(self):
        for native_mode in ('Multiplayer', 'Conquest', 'Bifrost'):
            h = Heap(); h.fields['state', 'gameType'] = 0
            if native_mode == 'Multiplayer':
                h.enums['mode'] = ('Multiplayer', [(923, 'variant')])
            else:
                h.enums['mode'] = (native_mode, [(13, 'ignored')] * (2 if native_mode == 'Conquest' else 1))
            self.assertEqual(capture(h, game_context={**CONTEXT, 'game_type': None})['status'],
                             'unsupported_context')
        for pointer in (None, False, 0.0, 'damaged_enum'):
            h = Heap(); h.fields['state', 'gameType'] = pointer
            h.types['damaged_enum'] = 'ent.Unit'
            with self.subTest(pointer=pointer), self.assertRaises(module.LayoutError):
                capture(h, game_context={**CONTEXT, 'game_type': None})

    def test_game_type_null_to_enum_and_enum_to_null_races_are_rejected(self):
        h = Heap(); h.fields['state', 'gameType'] = 0
        h.changes['field', 'state', 'gameType'] = lambda n, old: 'game_type' if n >= 3 else old
        with self.assertRaises(module.LayoutError):
            capture(h, game_context={**CONTEXT, 'game_type': None})
        h = Heap()
        h.changes['field', 'state', 'gameType'] = lambda n, old: 0 if n >= 4 else old
        with self.assertRaises(module.LayoutError):
            capture(h)

    def test_wrong_profile_identity_and_unknown_id_rejected(self):
        for args in ({'profile': {**PROFILE, 'data_cdb_sha256': 'different'}},
                     {'identity': {}}, {'known_zone_ids': [1, 3]}, {'known_zone_ids': [1, 1]},
                     {'known_zone_ids': [True, 2]}):
            with self.subTest(args=args), self.assertRaises(module.LayoutError):
                capture(**args)

    def test_local_player_must_be_human_and_current_participant(self):
        for field in ('ai', 'roster'):
            h = Heap()
            if field == 'ai':h.fields['me', 'aiJob'] = 999
            else:h.arrays['players'] = ['foreign_player']
            with self.assertRaises(module.LayoutError):capture(h)

    def test_discovery_and_root_changes_reject_whole_optional_component(self):
        for key, replacement in ((('array', 'ids'), (1,)),
                                 (('field', 'game', 'state'), 'new_state')):
            h = Heap(); h.changes[key] = lambda n, old, v=replacement: v if n > 1 else old
            with self.assertRaises(module.LayoutError):capture(h)

    def test_stale_current_zone_or_object_reference_rejected(self):
        h = Heap(); h.fields['z1', 'game'] = 'previous_game'
        with self.assertRaises(module.LayoutError):capture(h)
        h = Heap(); h.fields['stone', 'zone'] = 'old_z1_same_numeric_id'
        row = capture(h)
        self.assertEqual(row['zones'][0]['resources_status'], 'unavailable')

    def test_missing_mineral_state_stays_null_and_not_depleted(self):
        h = Heap(); h.fields['stone', 'state'] = 0
        row = capture(h)['zones'][0]
        self.assertEqual(row['resources_status'], 'partial')
        self.assertIsNone(row['resources'][0]['deposits'][0]['remaining_contains_raw'])
        self.assertIsNone(row['resources'][0]['remaining_total_raw'])
        self.assertEqual(row['wildlife_status'], 'ok')

    def test_explicit_zero_is_distinct_and_fraction_kept_raw(self):
        for amount in (0., 12.25):
            h = Heap(); h.boxes[10001] = (6, amount)
            resource = capture(h)['zones'][0]['resources'][0]
            self.assertEqual(resource['remaining_total_raw'], amount)
            self.assertEqual(resource['modeled_ui_remaining_total'], module.math.ceil(amount))

    def test_mineral_race_isolated_from_wildlife_and_other_zones(self):
        h = Heap(); h.changes['box', 10001] = lambda n, old: 29. if n > 1 else old
        row = capture(h)
        self.assertEqual(row['zones'][0]['resources_status'], 'unavailable')
        self.assertEqual(row['zones'][0]['wildlife_status'], 'ok')
        self.assertEqual(row['zones'][1]['resources_status'], 'ok')

    def test_unit_race_isolated_from_minerals(self):
        h = Heap(); h.changes['field', 'wolf1', 'owner'] = lambda n, old: 'me' if n > 1 else old
        row = capture(h)['zones'][0]
        self.assertEqual(row['resources_status'], 'ok')
        self.assertEqual(row['wildlife_status'], 'unavailable')

    def test_grass_is_full_building_classification_not_icon_absence(self):
        for grass in ('TallGrass', 'HidingGrass'):
            h = Heap(); h.add_building('grass', grass, 'z1', uid=202)
            row = capture(h)['zones'][0]
            self.assertEqual(row['natural_attributes_status'], 'complete')
            self.assertTrue(row['tall_grass_present'] or row['hiding_grass_present'])
            self.assertEqual(row['wildlife_status'], 'partial')
            self.assertEqual(row['wildlife'], [])
            self.assertEqual(row['resources_status'], 'partial')
            self.assertEqual(row['resources'], [])

    def test_explicit_script_visibility_preserves_positive_subset_in_grass(self):
        h = Heap(); h.add_building('grass', 'TallGrass', 'z1', uid=202)
        h.fields['wolf1', 'scriptVisible'] = 10003
        row = capture(h)['zones'][0]
        self.assertEqual(row['wildlife_status'], 'partial')
        self.assertEqual(row['wildlife'], [{'kind': 'Wolf', 'count': 1}])

    def test_disabled_and_script_hidden_do_not_read_mineral_state(self):
        for field, value in (('enabled', False), ('scriptVisible', 10004)):
            h = Heap(); h.fields['stone', field] = value
            self.assertEqual(capture(h)['zones'][0]['resources'], [])
            self.assertNotIn(('stone', 'state'), h.reads)

    def test_garrisoned_or_active_cocoon_is_not_counted(self):
        h = Heap(); h.arrays['no_status'] = ['Garrisoned']
        self.assertEqual(capture(h)['zones'][0]['wildlife'], [])
        with patch.object(module, '_target_affect_end', return_value=60.):
            self.assertEqual(capture()['zones'][0]['wildlife'], [])

    def test_affect_end_raw_comparison_has_no_infinite_negative_sentinel(self):
        for expiry in (None, -1., 0., 50.):
            with self.subTest(expiry=expiry), patch.object(module, '_target_affect_end', return_value=expiry):
                row = capture()['zones'][0]
                self.assertEqual(row['wildlife_status'], 'ok')
                self.assertEqual(row['wildlife'], [{'kind': 'Bear', 'count': 0}, {'kind': 'Wolf', 'count': 2}])
        for expiry in (50.001, 1e30):
            with self.subTest(expiry=expiry), patch.object(module, '_target_affect_end', return_value=expiry):
                self.assertEqual(capture()['zones'][0]['wildlife'], [])

    def test_unsupported_derived_wolf_does_not_inherit_proven_dispatch(self):
        h = Heap(); h.types['wolf1'] = 'ent.CustomFoe'
        row = capture(h)['zones'][0]
        self.assertEqual(row['wildlife_status'], 'partial')
        self.assertEqual(row['wildlife'], [{'kind': 'Wolf', 'count': 1}])

    def test_shoal_override_requires_materialized_nonsea_branch(self):
        for sea in (0, 10003, 10004):
            h = Heap(); h.add_building('fish', 'Shoal', 'z2', name='ent.b.Shoal', uid=203)
            h.fields['fish', 'seaShoal'] = sea
            row = capture(h)['zones'][1]
            self.assertEqual(any(r['kind'] == 'Shoal' for r in row['resources']), sea == 10004)
            self.assertEqual(row['resources_status'], 'ok' if sea == 10004 else 'partial')

    def test_static_identity_mismatch_rejects_only_resource_component(self):
        h = Heap(); h.fields['stone_inf', 'flags'] = 0
        row = capture(h)['zones'][0]
        self.assertEqual(row['resources_status'], 'unavailable')
        self.assertEqual(row['wildlife_status'], 'ok')

    def test_all_eight_supported_resource_kinds_reach_output(self):
        h = Heap()
        for offset, kind in enumerate(('CircleStone', 'Fertility', 'IronDeposit',
                                       'Shoal', 'Grove', 'DeerSpawner'), 210):
            name = next(iter(module.NATURAL_TYPES[kind]))
            pointer = kind + '_object'
            h.add_building(pointer, kind, 'z2', name=name, uid=offset)
            if kind == 'Shoal': h.fields[pointer, 'seaShoal'] = 10004
            if kind == 'IronDeposit': h.fields[pointer, 'state'] = 'stone_state'
        row = capture(h)
        self.assertEqual({resource['kind'] for zone in row['zones'] for resource in zone['resources']},
                         set(module.KINDS))
        self.assertTrue(all(zone['resources_status'] == 'ok' for zone in row['zones']))

    def test_circle_stone_uses_lore_stone_factory_alias_before_circle_class(self):
        # This fixture is independent of NATURAL_TYPES: aliasKinds group 40
        # includes CircleStone, and factory #12534's LoreStone branch runs first.
        h = Heap(); h.add_building('circle', 'CircleStone', 'z2',
                                   name='ent.b.LoreStone', uid=211)
        row = capture(h)['zones'][1]
        self.assertEqual(row['resources_status'], 'ok')
        self.assertEqual({resource['kind'] for resource in row['resources']},
                         {'CircleStone', 'RuneStone'})
        circle = next(resource for resource in row['resources'] if resource['kind'] == 'CircleStone')
        self.assertEqual(circle['object_count'], 1)
        self.assertEqual(circle['deposits'], [])

    def test_duplicate_deposit_uid_across_zones_is_rejected(self):
        h = Heap(); h.add_building('stone2', 'Stones', 'z2', uid=200)
        h.fields['stone2', 'state'] = 'stone_state'
        with self.assertRaises(module.LayoutError):capture(h)

    def test_nullable_box_is_unboxed_by_type_not_exported_as_address(self):
        h = Heap(); h.boxes[10001] = (7, True)
        row = capture(h)['zones'][0]
        self.assertEqual(row['resources_status'], 'unavailable')
        self.assertEqual(row['resources'], [])

    def test_building_array_mutation_does_not_prove_grass_absence(self):
        h = Heap(); h.changes['array', 'z1b'] = lambda n, old: () if n == 2 else old
        row = capture(h)['zones'][0]
        self.assertEqual(row['resources_status'], 'unavailable')
        # A subsequent independently stable header scan can support wildlife;
        # the failed resource array is never filled from a cached earlier pass.
        self.assertEqual(row['wildlife_status'], 'ok')

    def test_same_enum_pointer_with_changed_native_variant_is_rejected(self):
        h = Heap()
        def change(n, old):
            if n > 2: h.enums['variant'] = ('Team3V3', [])
            return old
        h.changes['field', 'state', 'mode'] = change
        with self.assertRaises(module.LayoutError):capture(h)

    def test_clock_rollback_and_expiry_crossing_rejected(self):
        h = Heap(); h.changes['field', 'state', 'time'] = lambda n, old: 49. if n > 1 else old
        with self.assertRaises(module.LayoutError):capture(h)
        h = Heap(); h.changes['field', 'state', 'time'] = lambda n, old: 51. if n > 2 else old
        with patch.object(module, '_target_affect_end', return_value=50.5):
            self.assertEqual(capture(h)['zones'][0]['wildlife_status'], 'unavailable')


class EffectHeap:
    def __init__(self):
        self.fields = {('unit', 'evars'): 'evars', ('evars', 'entity'): 'unit',
                       ('evars', 'affectsTime'): 'proxy', ('proxy', 'map'): 'map', ('map', 'h'): 1000}
        self.names = {'evars': 'ent.EntityVariables', 'proxy': 'hxbit.MapData', 'map': 'haxe.ds.StringMap'}
        self.header = struct.pack('<QQQQQiiiii4x', 2000, 3000, 0, 4000, 0, 0, 2, 2, 2, 4)
        self.blocks = {1000: self.header, 2000: bytes((0, 1)), 3000: bytes((255, 255, 255, 255)),
                       4000: struct.pack('<QQQQQQQQ', 5000, 6000, 5001, 6001, 0, 0, 0, 0)}
        self.keys = {5000: 'GullveigCocoon', 5001: 'UnrelatedObjectEffect'}
        self.unboxed = []
    def field(self, p, n): return self.fields[p, n]
    def object_type(self, p):
        return SimpleNamespace(name=self.names.get(p, 'boxed'), kind=6 if p in (6000, 6001) else 11, address=p)
    def read(self, p, n):return self.blocks[p][:n]
    def utf16(self, p):return self.keys[p]
    def dynamic(self, p):
        if p != 6000:raise AssertionError('Unrelated effect payload must never be unboxed')
        self.unboxed.append(p);return 80.


class ExactEffectMapTests(unittest.TestCase):
    def test_only_requested_effect_payload_is_unboxed(self):
        h = EffectHeap()
        self.assertEqual(module._target_affect_end(h, 'unit', 'GullveigCocoon'), 80.)
        self.assertEqual(h.unboxed, [6000])
    def test_absent_target_is_not_default_other_effect(self):
        h = EffectHeap();h.keys[5000] = 'AnotherEffect'
        self.assertIsNone(module._target_affect_end(h, 'unit', 'GullveigCocoon'))
        self.assertEqual(h.unboxed, [])
    def test_invalid_live_chain_rejected(self):
        h = EffectHeap();h.blocks[3000] = bytes((0, 255, 255, 255))
        with self.assertRaises(module.LayoutError):module._target_affect_end(h, 'unit', 'GullveigCocoon')
    def test_wrong_effect_component_identity_rejected(self):
        h = EffectHeap();h.fields['evars', 'entity'] = 'other_unit'
        with self.assertRaises(module.LayoutError):module._target_affect_end(h, 'unit', 'GullveigCocoon')
    def test_nonfinite_effect_end_is_unavailable_not_inactive(self):
        for value in (float('inf'), float('-inf'), float('nan')):
            h = EffectHeap()
            with self.subTest(value=value), patch.object(h, 'dynamic', return_value=value), self.assertRaises(module.LayoutError):
                module._target_affect_end(h, 'unit', 'GullveigCocoon')


if __name__ == '__main__':
    unittest.main()
