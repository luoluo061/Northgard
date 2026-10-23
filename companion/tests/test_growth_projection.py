import copy
import unittest

from companion.core.growth_projection import (
    GrowthProgressTracker, SOURCE, HLBOOT_SHA256, DATA_CDB_SHA256, project_growth)


def fixture(when=100., progress=33., **inputs):
    parent = {
        'status': 'ok', 'session_key': 'session', 'capture_run_id': 'run',
        'capture_segment_id': 'run:1', 'local_player_entity_uid': 31,
        'local_player_identity': {'verified': True, 'role': 'player', 'entity_uid': 31,
                                  'is_ai': False, 'in_player_list': True},
        'clan': 'Bear', 'game_time_seconds': when, 'population': 10,
        'game_context': {'mode': {'kind': 'Singleplayer', 'args': [
            {'kind': 'FreeForAll', 'args': []}]},
            'bifrost_data_present': False, 'editor_present': False},
        'owned_zone_ids': [1], 'buildings': [{'uid': 100, 'kind': 'TownHall', 'zone_id': 1}],
    }
    native = {
        'resource_growth_progress': 0., 'current_happiness': 4., 'population_cache': 10,
        'population_cap_cache': 16, 'reserve_population_cache': 0, 'recruit_stack': 0.,
        'force_recruit': False, 'low_housing_timer': 0., 'low_happiness_timer': 0.,
        'no_population_growth_time': 0., 'has_conceded': False, 'player_game_over': False,
        'game_finished': False, 'debug_population_can_grow': True, 'growth_disabled_modifier': False,
        **inputs,
    }
    raw = {
        'schema_version': 1, 'status': 'partial', 'source': SOURCE,
        'hlboot_sha256': HLBOOT_SHA256, 'data_cdb_sha256': DATA_CDB_SHA256,
        'local_player_entity_uid': 31, 'clan': 'Bear',
        'session_key': 'session', 'capture_run_id': 'run', 'capture_segment_id': 'run:1',
        'time_before': when, 'time_after': when, 'game_time_seconds': when,
        'read_guard_issues': [], 'native_inputs': native,
        'town_halls': [{'uid': 100, 'zone_id': 1, 'growth_progress': progress,
                        'force_recruit': False, 'recruit_stack': 0., 'enabled': True}],
        'bonus_inputs': {'normal_map_tech_presence': {'Recruit': False, 'EndlessTide': False,
                         'PlotThrone': False}, 'special_tech_providers_present': False,
                         'active_meal_count': 0, 'conquest_bonus_count': 0, 'additional_rule_count': 0},
    }
    return raw, parent


def observed(when=100., progress=33., **inputs):
    raw, parent = fixture(when, progress, **inputs)
    return {**parent, 'population_growth': project_growth(raw, parent)}


class ProjectionTests(unittest.TestCase):
    def test_native_and_modeled_threshold_are_distinct(self):
        raw, parent = fixture()
        value = project_growth(raw, parent)
        self.assertEqual(value['status'], 'partial')
        self.assertIsNone(value['growth_bonus'])
        self.assertEqual(value['model']['next_recruit_threshold'], 105)
        self.assertAlmostEqual(value['town_halls'][0]['progress_fraction_model'], 33 / 105)
        self.assertIsNone(value['model']['formula_eta_game_seconds'])

    def test_unsupported_clan_no_fabricated_threshold(self):
        raw, parent = fixture()
        raw['clan'] = parent['clan'] = 'Rat'
        value = project_growth(raw, parent)
        self.assertIsNone(value['model']['next_recruit_threshold'])
        self.assertIsNone(value['town_halls'][0]['progress_fraction_model'])

    def test_special_unknown_or_missing_mode_never_gets_standard_threshold(self):
        contexts = [None, {}, {'mode': {'kind': 'Singleplayer', 'args': []}},
                    {'mode': {'kind': 'Singleplayer', 'args': [{'kind': 'Conquest', 'args': []}]},
                     'bifrost_data_present': False, 'editor_present': False},
                    {'mode': {'kind': 'Multiplayer', 'args': [{'kind': 'FreeForAll', 'args': []}]},
                     'bifrost_data_present': False, 'editor_present': False}]
        ordinary = fixture()[1]['game_context']
        contexts += [{**ordinary, 'bifrost_data_present': True},
                     {**ordinary, 'editor_present': True},
                     {k: v for k, v in ordinary.items() if k != 'bifrost_data_present'},
                     {k: v for k, v in ordinary.items() if k != 'editor_present'},
                     {'mode': 'Singleplayer', 'submode': 'FreeForAll',
                      'bifrost_data_present': False, 'editor_present': False}]
        for context in contexts:
            raw, parent = fixture()
            if context is None:
                del parent['game_context']
            else:
                parent['game_context'] = context
            value = project_growth(raw, parent)
            self.assertEqual(value['status'], 'partial')
            self.assertEqual(value['town_halls'][0]['growth_progress'], 33.)
            self.assertIsNone(value['model']['next_recruit_threshold'], context)
            self.assertIsNone(value['model']['threshold_source'], context)
            self.assertIsNone(value['town_halls'][0]['progress_fraction_model'], context)
            self.assertIn('growth_threshold_mode_unverified', value['reason_codes'])

    def test_verified_raven_ordinary_mode_has_its_audited_threshold(self):
        raw, parent = fixture()
        raw['clan'] = parent['clan'] = 'Raven'
        value = project_growth(raw, parent)
        self.assertEqual(value['model']['next_recruit_threshold'], 105)

    def test_cache_mismatch_never_overrides_parent_housing_population(self):
        raw, parent = fixture(population_cache=12)
        value = project_growth(raw, parent)
        self.assertEqual(value['model']['next_recruit_threshold'], 105)
        self.assertFalse(value['model']['population_cache_matches_parent'])

    def test_player_build_capture_and_clock_mismatch_rejected(self):
        for field, replacement in (
                ('hlboot_sha256', 'bad'), ('data_cdb_sha256', 'bad'),
                ('session_key', 'other'), ('capture_run_id', 'other'),
                ('capture_segment_id', 'other'), ('local_player_entity_uid', 32),
                ('clan', 'Raven'), ('time_after', 99), ('status', 'read_race'),
                ('read_guard_issues', ['race'])):
            raw, parent = fixture()
            raw[field] = replacement
            self.assertEqual(project_growth(raw, parent)['status'], 'rejected', field)

    def test_invalid_types_nan_foreign_hall_and_private_keys(self):
        raw, parent = fixture()
        raw['native_inputs']['population_cache'] = True
        self.assertEqual(project_growth(raw, parent)['status'], 'rejected')
        raw, parent = fixture(current_happiness=float('nan'))
        self.assertEqual(project_growth(raw, parent)['status'], 'rejected')
        raw, parent = fixture()
        raw['town_halls'][0]['uid'] = 777
        self.assertEqual(project_growth(raw, parent)['status'], 'rejected')
        raw, parent = fixture()
        for obj in (raw, raw['native_inputs'], raw['bonus_inputs'], raw['town_halls'][0]):
            obj['name'] = 'private-name'
        self.assertNotIn('private-name', str(project_growth(raw, parent)))

    def test_known_blocking_inputs_are_not_complete_reason(self):
        raw, parent = fixture(current_happiness=0., population_cap_cache=10,
                              growth_disabled_modifier=True, debug_population_can_grow=False)
        value = project_growth(raw, parent)
        self.assertEqual(len(value['model']['observed_blocking_inputs']), 4)
        self.assertFalse(value['model']['blocking_inputs_complete'])


class ProgressTrackerTests(unittest.TestCase):
    def test_observed_progress_slope_and_conditional_eta(self):
        tracker = GrowthProgressTracker()
        self.assertEqual(tracker.update(observed()), [])
        out = tracker.update(observed(102., 37.))[0]
        self.assertEqual(out['observed_progress_per_game_second'], 2.)
        self.assertEqual(out['observed_effective_bonus_estimate'], 1.)
        self.assertEqual(out['conditional_threshold_eta_game_seconds'], 34.)
        self.assertFalse(out['within_interval_changes_excluded'])

    def test_pause_does_not_count_wall_time(self):
        tracker = GrowthProgressTracker()
        tracker.update(observed())
        self.assertEqual(tracker.update(observed()), [])
        out = tracker.update(observed(101., 35.))[0]
        self.assertEqual(out['elapsed_game_seconds'], 1.)

    def test_birth_reset_or_changed_population_suppresses_slope(self):
        tracker = GrowthProgressTracker()
        tracker.update(observed(progress=104.))
        self.assertEqual(tracker.update(observed(101., 1.)), [])
        row = observed(102., 3., population_cache=11)
        row['population_growth']['model']['population_input'] = 11
        self.assertEqual(tracker.update(row), [])

    def test_happiness_modifiers_identity_gap_and_error_do_not_bridge(self):
        tracker = GrowthProgressTracker()
        tracker.update(observed())
        self.assertEqual(tracker.update(observed(101., 34., current_happiness=9.)), [])
        changed = observed(102., 35., current_happiness=9.)
        changed['capture_segment_id'] = changed['population_growth']['capture_segment_id'] = 'run:2'
        self.assertEqual(tracker.update(changed), [])
        self.assertEqual(tracker.update(observed(120., 36.)), [])
        self.assertEqual(tracker.update({'status': 'error'}), [])
        self.assertEqual(tracker.update(observed(121., 38.)), [])

    def test_zero_slope_no_infinite_eta(self):
        tracker = GrowthProgressTracker()
        tracker.update(observed())
        out = tracker.update(observed(101., 33.))[0]
        self.assertEqual(out['observed_progress_per_game_second'], 0.)
        self.assertIsNone(out['conditional_threshold_eta_game_seconds'])

    def test_unknown_mode_observes_growth_but_never_predicts_threshold_eta(self):
        tracker = GrowthProgressTracker()
        rows = []
        for when, progress in ((100., 33.), (102., 37.)):
            raw, parent = fixture(when, progress)
            del parent['game_context']
            rows.append({**parent, 'population_growth': project_growth(raw, parent)})
        tracker.update(rows[0])
        out = tracker.update(rows[1])[0]
        self.assertEqual(out['observed_progress_per_game_second'], 2.)
        self.assertIsNone(out['conditional_threshold_eta_game_seconds'])


if __name__ == '__main__':
    unittest.main()
