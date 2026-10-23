import copy
import hashlib
import json
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from research.telemetry.resource_caps import (
    VERIFIED_HASH, VERIFIED_CDB_HASH, LayoutError, derive_standard_caps,
    standard_cycle, read_native_constants,
    _ordinary_singleplayer_ffa,
)

PROFILE = {'hlboot_sha256': VERIFIED_HASH, 'data_cdb_sha256': VERIFIED_CDB_HASH}
CONSTANTS = {'BaseFoodStock': 500, 'FoodStockIncrease': 300,
             'FoodUpgradedStockIncrease': 500}


def caps(buildings=(), **extra):
    args = dict(clan='Bear', bifrost_present=False, conquest_bonus_count=0,
                buildings=buildings, constants=CONSTANTS, profile=PROFILE,
                ordinary_singleplayer_ffa=True)
    args.update(extra)
    return derive_standard_caps(**args)


class FoodCapTests(unittest.TestCase):
    def test_base_and_two_completed_silos(self):
        self.assertEqual(caps()['resources']['Food']['stock_cap'], 500)
        b = [{'uid': i, 'kind': 'FoodSilo', 'upgraded': 0,
              'force_upgraded': False} for i in (1, 2)]
        food = caps(b)['resources']['Food']
        self.assertEqual(food['stock_cap'], 1100)
        self.assertTrue(food['stock_cap_verified'])
        self.assertEqual(food['integration_order'], 'consumption_then_production')

    def test_upgraded_silo_is_total_500_increment(self):
        b = [{'uid': 1, 'kind': 'FoodSilo', 'upgraded': 1,
              'force_upgraded': False}]
        self.assertEqual(caps(b)['resources']['Food']['stock_cap'], 1000)

    def test_force_upgraded_flag_matches_native_isUpgraded(self):
        b = [{'uid': 1, 'kind': 'FoodSilo', 'upgraded': 0,
              'force_upgraded': True}]
        self.assertEqual(caps(b)['resources']['Food']['stock_cap'], 1000)

    def test_construction_targeting_silo_not_counted(self):
        self.assertEqual(caps([{'kind': 'Construction', 'target_kind': 'FoodSilo'}])
                         ['resources']['Food']['stock_cap'], 500)

    def test_special_modes_are_unknown(self):
        for extra in ({'clan': 'Squirrel'}, {'bifrost_present': True},
                      {'bifrost_present': None}, {'conquest_bonus_count': 1},
                      {'conquest_bonus_count': None}, {'conquest_bonus_count': False},
                      {'conquest_bonus_count': 0.0}):
            self.assertFalse(caps(**extra)['resources']['Food']['stock_cap_verified'])

    def test_raven_supported(self):
        self.assertTrue(caps(clan='Raven')['resources']['Food']['stock_cap_verified'])

    def test_other_caps_are_verified_no_finite_limit_not_zero(self):
        for key in ('Wood', 'Money', 'Stone', 'Iron', 'Lore'):
            row = caps()['resources'][key]
            self.assertIsNone(row['stock_cap'])
            self.assertTrue(row['stock_cap_verified'])
            self.assertEqual(row['resource'], key)
            self.assertEqual(row['stock_cap_status'], 'no_finite_limit_verified')
            self.assertEqual(row['stock_cap_source'], 'verified_native_no_finite_limit')
            self.assertIsNone(row['integration_order'])

    def test_other_caps_require_explicit_ordinary_mode_verification(self):
        for flag in (False, None, 1, 'Singleplayer'):
            result = caps(ordinary_singleplayer_ffa=flag)
            for key in ('Wood', 'Money', 'Stone', 'Iron', 'Lore'):
                self.assertFalse(result['resources'][key]['stock_cap_verified'])
                self.assertIsNone(result['resources'][key]['stock_cap'])

    def test_live_mode_decoder_requires_singleplayer_free_for_all(self):
        class H:
            editor = False
            def field(self, address, name):
                return self.editor if name == 'editor' else 'mode'
            def object_type(self, address):
                return SimpleNamespace(name='enum:PlayMode' if address == 'mode' else 'enum:MultiMode')
            def type_info(self, address):
                return SimpleNamespace(name='enum:MultiMode')
        h = H()
        with patch('research.telemetry.runtime_decode.enum_values', side_effect=[
                ('Singleplayer', [(923, 'multi')]), ('FreeForAll', [])]):
            self.assertTrue(_ordinary_singleplayer_ffa(h, 'state'))
        with patch('research.telemetry.runtime_decode.enum_values', return_value=('Campaign', [])):
            self.assertFalse(_ordinary_singleplayer_ffa(h, 'state'))
        h.editor = True
        self.assertFalse(_ordinary_singleplayer_ffa(h, 'state'))

    def test_verified_cdb_group_five_excludes_core_stocks(self):
        path = Path(__file__).resolve().parents[1] / 'local_inventory/extracted/data.cdb'
        if not path.is_file():
            self.skipTest('Optional maintainer asset audit; full game data is intentionally not shipped')
        raw = path.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), VERIFIED_CDB_HASH)
        sheet = next(s for s in json.loads(raw)['sheets'] if s['name'] == 'resource')
        separators = {s['id']: i for i, s in enumerate(sheet['separators'])}
        group = -1
        ingredients = []
        for row in sheet['lines']:
            if row['id'] in separators:
                group = separators[row['id']]
            if group == 5:
                ingredients.append(row['id'])
        self.assertEqual(ingredients, ['BigGame', 'FreshwaterFish', 'Vegetables', 'Mushrooms'])
        self.assertTrue(set(ingredients).isdisjoint(('Food', 'Wood', 'Money', 'Stone', 'Iron', 'Lore')))

    def test_invalid_build_or_data_fails_closed(self):
        for key in PROFILE:
            profile = {**PROFILE, key: 'other'}
            with self.assertRaises(LayoutError):
                caps(profile=profile)

    def test_ambiguous_or_duplicate_building_fails_closed(self):
        b = {'uid': 1, 'kind': 'FoodSilo', 'upgraded': 0, 'force_upgraded': False}
        for buildings in ([b, copy.copy(b)], [{k: v for k, v in b.items() if k != 'force_upgraded'}]):
            with self.assertRaises(LayoutError):
                caps(buildings)

    def test_native_constants_use_metadata_and_check_type(self):
        class Info:
            name = 'Const'
            parameter = 200
        class FakeH:
            def type_info(self, address):
                self.type_address = address
                return Info()
            def ptr(self, address):
                return {256: 300, 300: 400}[address]
            def object_type(self, address):
                return type('StaticInfo', (), {'name': '$Const'})()
            def field(self, address, name):
                return CONSTANTS[name]
        h = FakeH()
        self.assertEqual(read_native_constants(h, 1000, profile=PROFILE), CONSTANTS)
        self.assertEqual(h.type_address, 1000 + 46856 * 32)


class CycleOrderTests(unittest.TestCase):
    def test_food_cap_cycle_counter_is_clipped_received_not_potential(self):
        # Consumed first, so a full granary can receive replacement food.
        result = standard_cycle(stock=1100, gross=8, consumption=5, dt=1, cap=1100)
        self.assertEqual(result['stock_after'], 1100)
        self.assertEqual(result['positive_counter_delta'], 5)
        self.assertEqual(result['potential_production'], 8)
        self.assertEqual(result['overflow'], 3)

    def test_cap_crossing_and_ordinary_cycle(self):
        result = standard_cycle(stock=1099, gross=8, consumption=5, dt=1, cap=1100)
        self.assertEqual(result['stock_delta'], 1)
        self.assertEqual(result['positive_counter_delta'], 6)
        result = standard_cycle(stock=100, gross=3, consumption=5, dt=1, cap=1100)
        self.assertEqual(result['stock_after'], 98)
        self.assertEqual(result['positive_counter_delta'], 3)

    def test_negative_stock_is_not_implicitly_clamped(self):
        result = standard_cycle(stock=1, gross=0, consumption=5, dt=1, cap=500)
        self.assertEqual(result['stock_after'], -4)

    def test_unsupported_above_cap_or_negative_rate_rejected(self):
        for args in (dict(stock=1101), dict(gross=-1), dict(consumption=-1),
                     dict(dt=-1), dict(cap=0), dict(stock=float('nan'))):
            values = dict(stock=1100, gross=8, consumption=5, dt=1, cap=1100)
            values.update(args)
            with self.assertRaises(LayoutError):
                standard_cycle(**values)


if __name__ == '__main__':
    unittest.main()
