import copy
import json
import unittest

from cost_quotes import quote_feast, HLBOOT_SHA256, DATA_CDB_SHA256, REQUIRED_FEAST_INPUTS


class FeastQuotes(unittest.TestCase):
    def setUp(self):
        self.operations = {'status': 'ok', 'feast': {
            'feast_count': 4, 'free_feast': 0, 'free_feast_count': 0,
            'last_feast': 2959.0, 'free_feast_time': 0.0,
            'team_leader_is_self': True, 'has_relic_goat': False,
            'has_relic_chalice': False, 'has_trial_faith': False,
            'hunt_cost_reduction': 0.0, 'goat_cost_reduction': 0.0,
            'living_legend_cost_reduction': 0.0,
        }}
        self.context = {'mode': {'kind': 'Singleplayer', 'args': [{'kind': 'FreeForAll', 'args': []}]},
                        'bifrost_data_present': False, 'editor_present': False}
        self.profile = {'hlboot_sha256': HLBOOT_SHA256, 'data_cdb_sha256': DATA_CDB_SHA256}

    def quote(self, **kwargs):
        return quote_feast(kwargs.pop('operations', self.operations), clan=kwargs.pop('clan', 'Bear'),
                           game_context=kwargs.pop('game_context', self.context),
                           profile=kwargs.pop('profile', self.profile), **kwargs)

    def test_paid_progression_including_510_and_760(self):
        for count, amount in enumerate((100, 150, 230, 340, 510, 760)):
            with self.subTest(count=count):
                self.operations['feast']['feast_count'] = count
                row = self.quote()
                self.assertEqual(row['status'], 'verified')
                self.assertEqual(row['cost'], [{'resource': 'Food', 'amount': amount}])

    def test_explicit_free_feast(self):
        self.operations['feast']['free_feast'] = 1
        self.assertEqual(self.quote()['cost'], [{'resource': 'Food', 'amount': 0}])

    def test_each_missing_input_refuses_instead_of_defaulting(self):
        for key in REQUIRED_FEAST_INPUTS:
            with self.subTest(key=key):
                ops = copy.deepcopy(self.operations)
                del ops['feast'][key]
                self.assertEqual(self.quote(operations=ops)['status'], 'unsupported')

    def test_special_unknown_and_invalid_inputs_refuse(self):
        mutations = [('team_leader_is_self', False), ('has_trial_faith', True),
                     ('has_relic_goat', True), ('has_relic_chalice', True),
                     ('goat_cost_reduction', .1), ('hunt_cost_reduction', .2),
                     ('living_legend_cost_reduction', .1), ('feast_count', True),
                     ('free_feast', -1), ('last_feast', float('nan')),
                     ('free_feast_time', float('inf')), ('has_trial_faith', 0)]
        for key, value in mutations:
            with self.subTest(key=key):
                ops = copy.deepcopy(self.operations)
                ops['feast'][key] = value
                row = self.quote(operations=ops)
                self.assertEqual(row['status'], 'unsupported')
                self.assertEqual(row['cost'], [])
                json.dumps(row, allow_nan=False)

    def test_wrong_build_clan_or_context_refuses(self):
        cases = [{'profile': {}}, {'profile': {**self.profile, 'data_cdb_sha256': 'changed'}},
                 {'clan': 'Raven'}, {'game_context': {}},
                 {'game_context': {**self.context, 'editor_present': True}},
                 {'operations': {**self.operations, 'status': 'read_race'}}]
        for case in cases:
            with self.subTest(case=case):
                self.assertEqual(self.quote(**case)['status'], 'unsupported')

    def test_does_not_mutate_snapshot(self):
        before = copy.deepcopy(self.operations)
        self.quote()
        self.assertEqual(before, self.operations)

    def test_large_count_safely_refuses_native_overflow(self):
        self.operations['feast']['feast_count'] = 10000
        self.assertEqual(self.quote()['status'], 'unsupported')


if __name__ == '__main__':
    unittest.main()
