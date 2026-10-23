import copy
import unittest

from companion.core.labor_transitions import LaborTransitionTracker


def unit(**changes):
    return {'uid': 1, 'kind': 'Villager', 'zone_id': 1,
            'assign_to': {'uid': 100}, 'going_to': None, 'target_zone_id': None,
            'assigned_job': {'kind': 'Produce'}, 'cur_job': {'kind': 'Produce'},
            'produce': 'Food', 'enabled': True, 'has_no_work': False,
            'position': {'x': 0., 'y': 0.}, **changes}


def observation(when=100., rows=None, **changes):
    return {'schema_version': 1, 'observation_type': 'PlayerObservation', 'status': 'ok',
            'scope': 'local_player_and_owned_zones', 'quality': {'identity_verified': True},
            'session_key': 'session', 'capture_run_id': 'run', 'capture_segment_id': 'run:1',
            'local_player_entity_uid': 31, 'clan': 'Bear',
            'game_time_seconds': when, 'discovered_zone_ids': [1, 2], 'owned_zone_ids': [1, 2],
            'buildings': [{'uid': 100, 'kind': 'TownHall', 'zone_id': 1},
                          {'uid': 200, 'kind': 'Fields', 'zone_id': 2}],
            'units': rows if rows is not None else [unit()], **changes}


def assigned(**changes):
    return unit(assign_to={'uid': 200}, going_to={'uid': 200}, target_zone_id=2,
                produce=None, has_no_work=True, cur_job={'kind': 'Move'}, **changes)


class LaborTransitionTests(unittest.TestCase):
    def test_uid_linked_assignment_movement_occupation_and_candidate(self):
        tracker = LaborTransitionTracker()
        self.assertEqual(tracker.update(observation()), [])
        a = tracker.update(observation(101., [assigned()]))
        self.assertEqual(a[0]['phase'], 'assignment_changed_observed')
        self.assertEqual(a[0]['assignment_observed_window']['previous_game_seconds'], 100.)
        self.assertEqual(a[0]['before']['produce'], 'Food')
        b = tracker.update(observation(103., [assigned(position={'x': 8., 'y': 9.})]))
        self.assertEqual(b[0]['phase'], 'location_changed_observed')
        farmer = unit(kind='Farmer', zone_id=2, assign_to={'uid': 200},
                      produce='Wheat', position={'x': 10., 'y': 10.})
        c = tracker.update(observation(105., [farmer]))
        phases = {row['phase'] for row in c}
        self.assertIn('occupation_changed_observed', phases)
        self.assertIn('production_candidate_resumed_observed', phases)
        self.assertNotIn('assignment_changed_observed', phases)
        completed = next(row for row in c if row['phase'] == 'production_candidate_resumed_observed')
        self.assertEqual(completed['elapsed_from_assignment_game_seconds_bounds'], {'lower': 2., 'upper': 5.})
        self.assertFalse(completed['actual_resource_production_confirmed'])
        self.assertEqual({row['transition_id'] for row in a + b + c}, {a[0]['transition_id']})
        self.assertEqual(tracker.finish(), [])

    def test_stale_produce_at_assignment_is_not_confirmed_arrival(self):
        tracker = LaborTransitionTracker()
        tracker.update(observation())
        row = unit(assign_to={'uid': 200})
        events = tracker.update(observation(101., [row]))
        self.assertNotIn('production_candidate_resumed_observed', [e['phase'] for e in events])
        self.assertEqual(tracker.update(observation(102., [row])), [])
        self.assertEqual(tracker.finish()[0]['phase'], 'tracking_incomplete')

    def test_already_changed_resource_can_be_observed_in_same_assignment_window(self):
        tracker = LaborTransitionTracker()
        tracker.update(observation())
        row = unit(assign_to={'uid': 200}, kind='Farmer', produce='Wheat')
        events = tracker.update(observation(101., [row]))
        candidate = next(e for e in events if e['phase'] == 'production_candidate_resumed_observed')
        self.assertEqual(candidate['milestone_observed_window']['previous_game_seconds'], 100.)
        self.assertEqual(candidate['elapsed_from_assignment_game_seconds_bounds'], {'lower': 0., 'upper': 1.})

    def test_pause_uses_same_game_time_and_no_wall_clock(self):
        tracker = LaborTransitionTracker()
        tracker.update(observation())
        events = tracker.update(observation(100., [assigned()]))
        self.assertEqual(events[0]['assignment_observed_window']['sample_interval_game_seconds'], 0.)
        self.assertFalse(events[0]['assignment_observed_window']['lower_exclusive'])
        self.assertEqual(tracker.update(observation(100., [assigned()])), [])
        farmer = unit(assign_to={'uid': 200}, kind='Farmer', produce='Wheat')
        candidate = next(e for e in tracker.update(observation(101., [farmer]))
                         if e['phase'] == 'production_candidate_resumed_observed')
        self.assertEqual(candidate['elapsed_from_assignment_game_seconds_bounds'], {'lower': 0., 'upper': 1.})

    def test_reassignment_abandons_previous_pending_stage(self):
        tracker = LaborTransitionTracker()
        tracker.update(observation())
        a = tracker.update(observation(101., [assigned()]))[0]
        b = tracker.update(observation(102., [unit(produce=None, has_no_work=True)]))
        self.assertEqual(b[0]['reason_code'], 'new_assignment_before_confirmed_output')
        self.assertNotEqual(a['transition_id'], b[1]['transition_id'])

    def test_errors_session_run_segment_rollback_and_gap_never_bridge(self):
        for changes in ({'status': 'error'}, {'session_key': 'other'},
                        {'capture_run_id': 'other'}, {'capture_segment_id': 'other'},
                        {'local_player_entity_uid': 32}, {'clan': 'Raven'},
                        {'game_time_seconds': 99.}, {'game_time_seconds': 200.}):
            tracker = LaborTransitionTracker()
            tracker.update(observation())
            tracker.update(observation(101., [assigned()]))
            row = observation(102., [assigned()], **changes)
            events = tracker.update(row)
            self.assertEqual(events[0]['phase'], 'tracking_incomplete', changes)
            self.assertFalse(events[0]['actual_resource_production_confirmed'])
            self.assertNotIn('production_candidate_resumed_observed', [e['phase'] for e in events])

    def test_unit_disappearance_and_appearance_do_not_invent_death_birth(self):
        tracker = LaborTransitionTracker()
        tracker.update(observation())
        tracker.update(observation(101., [assigned()]))
        events = tracker.update(observation(102., []))
        self.assertEqual(events[0]['reason_code'], 'unit_not_observed_cause_unknown')
        self.assertNotIn('death', str(events))
        self.assertEqual(tracker.update(observation(103., [unit(uid=2)])), [])

    def test_hero_bear_and_military_are_separate_without_population_claim(self):
        for kind, category in [('Maiden', 'hero'), ('Maiden02', 'hero'),
                               ('BearMaiden', 'special_armored_bear'),
                               ('AxeWielder', 'ordinary_military_kind'),
                               ('ShieldBearer', 'ordinary_military_kind')]:
            tracker = LaborTransitionTracker()
            tracker.update(observation(rows=[unit(kind=kind)]))
            events = tracker.update(observation(101., [assigned(kind=kind)]))
            self.assertEqual(events[0]['unit_class_before'], category)
            self.assertIn('unit_population_weight', events[0]['unknown_fields'])

    def test_native_runemaster_and_innkeeper_kinds_are_workers(self):
        for kind in ('RuneMaster', 'InnKeeper', 'Runemaster', 'Brewer'):
            with self.subTest(kind=kind):
                tracker = LaborTransitionTracker()
                tracker.update(observation(rows=[unit(kind=kind)]))
                events = tracker.update(observation(101., [assigned(kind=kind)]))
                self.assertEqual(events[0]['unit_class_before'], 'worker_kind')
                self.assertEqual(events[0]['unit_class_now'], 'worker_kind')

    def test_unknown_scope_invalid_reference_or_duplicates_fail_closed(self):
        for rows in ([unit(assign_to={'uid': 888, 'name': 'private'})],
                     [unit(), unit()], [unit(zone_id=777)]):
            tracker = LaborTransitionTracker()
            tracker.update(observation())
            self.assertEqual(tracker.update(observation(101., rows)), [])
        tracker = LaborTransitionTracker()
        tracker.update(observation())
        self.assertEqual(tracker.update(observation(101., [assigned()], scope='all_players')), [])

    def test_private_or_arbitrary_fields_never_copied(self):
        tracker = LaborTransitionTracker()
        tracker.update(observation(rows=[unit(name='private-name', owner='private-owner')]))
        row = assigned(name='private-name', unknown_args={'secret': 'secret'},
                       position={'x': 5., 'y': 3., 'account': 'private-account'})
        row['assign_to']['name'] = 'private-name'
        output = tracker.update(observation(101., [row]))
        for secret in ('private-name', 'private-owner', 'secret', 'private-account'):
            self.assertNotIn(secret, str(output))


if __name__ == '__main__':
    unittest.main()
