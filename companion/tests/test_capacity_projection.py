from copy import deepcopy
import unittest

from companion.tests.test_player_observation import snapshot_with_economy, MODULE


def capped_snapshot():
    row = snapshot_with_economy()
    row['buildings'].append({'uid': 21, 'kind': 'FoodSilo', 'zone_id': 87, 'upgraded': 0})
    proof = {'stock_cap': 800, 'stock_cap_verified': True,
             'stock_cap_source': 'verified_native_getMax_formula',
             'stock_cap_status': 'finite_limit_verified',
             'integration_order': 'consumption_then_production',
             'hlboot_sha256': MODULE._ECONOMY_BUILD_HASH, 'data_cdb_sha256': MODULE._ECONOMY_DATA_HASH,
             'components': {'base': 500, 'silo_multiplier': 1.,
                            'silos': [{'building_uid': 21, 'zone_id': 87, 'is_upgraded': False, 'capacity_added': 300}]}}
    row['economy']['resources']['Food'].update({key: proof[key] for key in
        ('stock_cap', 'stock_cap_verified', 'stock_cap_source', 'stock_cap_status', 'integration_order')})
    row['economy']['resources']['Food']['stock_cap_evidence'] = proof
    return row


class CapacityProjectionTests(unittest.TestCase):
    def test_own_complete_building_evidence_preserves_formula(self):
        result = MODULE.project_player_observation(capped_snapshot())['economy']['resources']['Food']
        self.assertTrue(result['stock_cap_verified'])
        self.assertEqual(result['stock_cap'], 800)

    def test_foreign_omitted_or_duplicate_silo_cannot_establish_verified_cap(self):
        for mutation in ('foreign', 'omitted', 'duplicate', 'wrong_hash'):
            row = capped_snapshot()
            proof = row['economy']['resources']['Food']['stock_cap_evidence']
            if mutation == 'foreign': proof['components']['silos'][0]['building_uid'] = 999
            elif mutation == 'omitted': proof['components']['silos'] = []
            elif mutation == 'duplicate': proof['components']['silos'] *= 2
            else: proof['data_cdb_sha256'] = 'unknown'
            output = MODULE.project_player_observation(row)
            self.assertFalse(output['economy']['resources']['Food']['stock_cap_verified'])
            self.assertNotIn('stock_cap_components', output['economy']['resources']['Food'])

    def test_capacity_change_during_read_remains_a_race(self):
        row = capped_snapshot()
        row['economy']['read_guard_issues'] = ['resource_capacity_changed_during_read']
        output = MODULE.project_player_observation(row)['economy']
        self.assertEqual(output['status'], 'read_race')

    def test_known_absence_of_hard_cap_is_none_not_zero_for_five_resources(self):
        row = capped_snapshot()
        row['game_context'] = {'mode': {'kind': 'Singleplayer', 'args': [{'kind': 'FreeForAll', 'args': []}]},
                               'bifrost_data_present': False, 'editor_present': False}
        limits = {}
        for name in ('Wood', 'Money', 'Stone', 'Iron', 'Lore'):
            proof = {'resource': name, 'stock_cap': None, 'stock_cap_verified': True,
                     'stock_cap_source': 'verified_native_no_finite_limit',
                     'stock_cap_status': 'no_finite_limit_verified',
                     'hlboot_sha256': MODULE._ECONOMY_BUILD_HASH, 'data_cdb_sha256': MODULE._ECONOMY_DATA_HASH}
            limits[name] = proof
            if name in row['economy']['resources']:
                row['economy']['resources'][name].update({**proof, 'stock_cap_evidence': proof})
        row['economy']['resource_limits'] = {'resources': limits}
        output = MODULE.project_player_observation(row)['economy']
        for name in limits:
            self.assertTrue(output['resource_limits'][name]['stock_cap_verified'])
            self.assertIsNone(output['resource_limits'][name]['stock_cap'])
        row['game_context']['mode'] = {'kind': 'Multiplayer', 'args': []}
        self.assertFalse(MODULE.project_player_observation(row)['economy']['resource_limits']['Stone']['stock_cap_verified'])


if __name__ == '__main__':
    unittest.main()
