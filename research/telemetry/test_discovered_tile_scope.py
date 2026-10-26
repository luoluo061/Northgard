"""Legacy probe discovered-tile boundary, using invented stored fields only."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
import live_reader as module


class Heap:
    def __init__(self):
        self.calls = []
        self.types = {'world': 'ent.player.WorldComponent',
                      **{f'z{i}': 'ent.Zone' for i in range(1, 5)}}
        self.fields = {('state', 'me'): 'me', ('me', 'world'): 'world',
                       ('world', 'discoveredIds'): 'discovered',
                       ('state', 'zones'): 'zones',
                       ('z1', 'id'): 1, ('z1', 'owner'): 'me',
                       ('z1', 'maxBuildings'): 3, ('z1', 'buildings'): 'own_buildings',
                       ('z2', 'id'): 2, ('z2', 'owner'): 'foreign',
                       ('z3', 'id'): 3, ('z3', 'owner'): None,
                       ('z4', 'id'): 4, ('z4', 'owner'): 'foreign',
                       ('b1', 'zone'): 'z1', ('b1', 'kind'): 'TownHall',
                       ('b2', 'zone'): 'z1', ('b2', 'kind'): 'Forest'}
        self.arrays = {'zones': ['z1', 'z2', 'z3', 'z4'],
                       'discovered': [1, 2, 3], 'own_buildings': ['b1', 'b2']}
        self.after_field = None
        self.after_objects = None

    def field(self, pointer, name):
        self.calls.append(('field', pointer, name))
        value = self.fields[pointer, name]
        if self.after_field:
            self.after_field(pointer, name)
        return value

    def object_type(self, pointer):
        self.calls.append(('type', pointer))
        return SimpleNamespace(name=self.types[pointer])

    def int_array(self, pointer):
        self.calls.append(('int_array', pointer))
        return list(self.arrays[pointer])

    def objects(self, _heap, pointer, **_kwargs):
        self.calls.append(('objects', pointer))
        value = list(self.arrays[pointer])
        if self.after_objects:
            self.after_objects(pointer)
        return value


def read(heap, discovered=None):
    if discovered is None:
        discovered = [1, 2, 3]
    with patch.object(module, 'objects', side_effect=heap.objects), \
         patch.object(module, 'string', side_effect=lambda _, value: value), \
         patch.object(module, 'find_pid', side_effect=AssertionError('No process access')), \
         patch.object(module, 'Reader', side_effect=AssertionError('No process access')):
        return module.read_discovered_tiles(heap, 'state', 'me', discovered)


class DiscoveredTileScopeTests(unittest.TestCase):
    def test_only_own_contents_and_non_owned_ids(self):
        heap = Heap()
        self.assertEqual(read(heap), [
            {'id': 1, 'knowledge_scope': 'owned_zone', 'owner': 'self',
             'base_building_slots': 3, 'buildings': ['TownHall', 'Forest']},
            {'id': 2, 'knowledge_scope': 'discovered_id_only', 'current_visibility': 'unverified'},
            {'id': 3, 'knowledge_scope': 'discovered_id_only', 'current_visibility': 'unverified'}])
        for zone in ('z2', 'z3'):
            self.assertNotIn(('field', zone, 'buildings'), heap.calls)
            self.assertNotIn(('field', zone, 'maxBuildings'), heap.calls)
        self.assertFalse(any(call[0] == 'field' and call[1] == 'z4' and call[2] != 'id'
                             for call in heap.calls))

    def test_foreign_and_unowned_contents_are_never_read(self):
        heap = Heap()
        for zone in ('z2', 'z3'):
            heap.fields[zone, 'maxBuildings'] = 'private'
            heap.fields[zone, 'buildings'] = 'forbidden_array'
        self.assertEqual(read(heap)[1:], read(Heap())[1:])
        self.assertNotIn(('objects', 'forbidden_array'), heap.calls)

    def test_non_owned_output_does_not_reveal_owner_category(self):
        heap = Heap()
        before = read(heap)
        heap.fields['z2', 'owner'] = None
        heap.fields['z3', 'owner'] = 'another_foreign_player'
        self.assertEqual(read(heap), before)

    def test_missing_duplicate_known_ids_or_zone_objects_fail_closed(self):
        for mutation in ('missing', 'duplicate_id', 'duplicate_pointer'):
            with self.subTest(mutation=mutation):
                heap = Heap()
                if mutation == 'missing':
                    heap.arrays['zones'].remove('z2')
                elif mutation == 'duplicate_id':
                    heap.fields['z3', 'id'] = 2
                else:
                    heap.arrays['zones'].append('z2')
                with self.assertRaises(module.LayoutError):
                    read(heap)

    def test_invalid_or_not_discovered_requests_fail_closed(self):
        for requested in ([1, 1], [True], [-1], [2**31], ['1'], [1, 4], (1, 2, 3)):
            with self.subTest(requested=requested), self.assertRaises(module.LayoutError):
                read(Heap(), requested)

    def test_changed_knowledge_player_or_world_fails_closed(self):
        for mutation in ('discovered', 'player', 'world'):
            with self.subTest(mutation=mutation):
                heap = Heap()
                def mutate(pointer, name):
                    if (pointer, name) == ('b2', 'kind'):
                        if mutation == 'discovered': heap.arrays['discovered'] = [1, 2]
                        elif mutation == 'player': heap.fields['state', 'me'] = 'foreign'
                        else: heap.fields['me', 'world'] = 'foreign_world'
                heap.after_field = mutate
                with self.assertRaises(module.LayoutError):
                    read(heap)

    def test_initial_player_world_and_zone_types_are_checked(self):
        for mutation in ('player', 'world', 'zone'):
            with self.subTest(mutation=mutation):
                heap = Heap()
                if mutation == 'player': heap.fields['state', 'me'] = 'foreign'
                elif mutation == 'world': heap.types['world'] = 'WrongWorld'
                else: heap.types['z2'] = 'WrongZone'
                with self.assertRaises(module.LayoutError):
                    read(heap)

    def test_own_owner_race_before_contents_prevents_array_read(self):
        heap = Heap()
        def mutate(pointer, name):
            if (pointer, name) == ('z1', 'maxBuildings'):
                heap.fields['z1', 'owner'] = 'foreign'
        heap.after_field = mutate
        with self.assertRaises(module.LayoutError):
            read(heap)
        self.assertNotIn(('field', 'z1', 'buildings'), heap.calls)

    def test_own_owner_race_during_kind_read_rejects_entire_result(self):
        heap = Heap()
        def mutate(pointer, name):
            if (pointer, name) == ('b1', 'kind'):
                heap.fields['z1', 'owner'] = 'foreign'
        heap.after_field = mutate
        with self.assertRaises(module.LayoutError):
            read(heap)

    def test_non_owned_owner_race_does_not_read_its_contents(self):
        heap = Heap()
        def mutate(pointer, name):
            if (pointer, name) == ('z2', 'owner'):
                heap.fields['z2', 'owner'] = 'me'
        heap.after_field = mutate
        with self.assertRaises(module.LayoutError):
            read(heap)
        self.assertNotIn(('field', 'z2', 'buildings'), heap.calls)

    def test_foreign_building_pointer_rejected_before_kind(self):
        heap = Heap()
        heap.fields['b1', 'zone'] = 'z2'
        with self.assertRaises(module.LayoutError):
            read(heap)
        self.assertNotIn(('field', 'b1', 'kind'), heap.calls)

    def test_building_membership_change_or_duplicate_fails_closed(self):
        for mutation in ('parent', 'array', 'duplicate'):
            with self.subTest(mutation=mutation):
                heap = Heap()
                def mutate(pointer, name):
                    if (pointer, name) == ('b2', 'kind'):
                        if mutation == 'parent': heap.fields['b1', 'zone'] = 'z2'
                        elif mutation == 'array': heap.arrays['own_buildings'] = ['b2']
                heap.after_field = mutate
                if mutation == 'duplicate': heap.arrays['own_buildings'].append('b1')
                with self.assertRaises(module.LayoutError):
                    read(heap)

    def test_map_array_or_known_zone_id_change_fails_closed(self):
        for mutation in ('array', 'id', 'boolean_id'):
            with self.subTest(mutation=mutation):
                heap = Heap()
                def mutate(pointer, name):
                    if (pointer, name) == ('b2', 'kind'):
                        if mutation == 'array': heap.arrays['zones'].remove('z4')
                        elif mutation == 'id': heap.fields['z1', 'id'] = 50
                        else: heap.fields['z1', 'id'] = True
                heap.after_field = mutate
                with self.assertRaises(module.LayoutError):
                    read(heap)

    def test_invalid_own_capacity_and_building_kind_do_not_become_empty(self):
        for field, value in ((('z1', 'maxBuildings'), True),
                             (('z1', 'maxBuildings'), -1),
                             (('b1', 'kind'), None), (('b1', 'kind'), '')):
            with self.subTest(field=field, value=value):
                heap = Heap()
                heap.fields[field] = value
                with self.assertRaises(module.LayoutError):
                    read(heap)


if __name__ == '__main__':
    unittest.main()
