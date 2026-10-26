import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest

CORE_PATH = Path(__file__).resolve().parents[1] / 'core'
sys.path.insert(0, str(CORE_PATH))
from operation_ledger import OperationLedger

HASH = 'db0459d251879698420f58f6819e0ab2f75a3cde6e65611f2ad723f3b4b7ef76'
IDS = {'session_key':'match-a', 'capture_run_id':'run-a', 'capture_segment_id':'run-a:1'}


def snap(t=10, *, debit=None):
    debit = debit or {}
    resources = {r:{'stock':600 + t-10 - debit.get(r,0), 'cached_at_game_time':t,
        'age_game_seconds':0., 'net_per_game_second':1., 'gross_aggregate_per_game_second':2.,
        'consumption_per_game_second':1., 'total_produced_counter':20+(t-10)*2,
        'stock_cap':None} for r in ('Food','Wood','Money')}
    stocks = {r:d['stock'] for r,d in resources.items()}
    stocks.update({r:30-debit.get(r,0) for r in ('Stone','Iron','Lore')})
    return {'observation_type':'PlayerObservation', 'status':'ok', **IDS,
        'game_time_seconds':t, 'local_player_entity_uid':31,
        'sample_span_game_seconds':.01, 'stocks':stocks,
        'owned_zone_ids':[179], 'discovered_zone_ids':[179,82], 'buildings':[], 'own_events':[],
        'economy':{'status':'ok', **IDS, 'game_time_seconds':t, 'sample_span_game_seconds':.01,
            'time_before':t, 'time_after':t+.01, 'hlboot_sha256':HASH,
            'resource_table_fingerprint':'verified-table', 'resources':resources},
        'operations':{'schema_version':1, 'source':'native_own_operation_state_read_only',
            'status':'ok', **IDS, 'local_player_entity_uid':31, 'game_time_seconds':t,
            'time_before':t,'time_after':t+.01,'sample_span_game_seconds':.01,
            'unknown_fields':[], 'colonizations':[], 'building_costs':[],
            'feast':{'feast_count':4,'free_feast':0,'free_feast_count':0,'last_feast':9},
            'feast_quote':{'status':'verified','source':'verified_native_getFeastCost_formula',
                'cost':[{'resource':'Food','amount':510}], 'formula_branch':'paid_feast_zero_modifiers'}}}


def colony(*, active=True, amount=40, extra_status='no_record'):
    return {'zone_id':82,'started_by_self':active,
        'paid_cost':[{'resource':'Food','amount':amount}], 'paid_cost_record_status':'stored_record',
        'extra_paid_cost':None, 'extra_paid_cost_record_status':extra_status}


def construction(uid=9):
    return {'uid':uid,'zone_id':179,'kind':'Construction','target_kind':'House','is_construction':True}


def add_construction(row, *, uid=9, cost=None, free=False, status='stored_record'):
    row['buildings'].append(construction(uid))
    row['operations']['building_costs'].append({'uid':uid,'zone_id':179,'was_free':free,
        'cost_record_status':status,'cost':[{'resource':'Wood','amount':50}] if cost is None else cost})


def own_event(index=1, kind='Feast', time=10):
    return {'index':index,'kind':kind,'time':time,'actor':'self','source':'native_own_success_event'}


def action(a,b, *, states=(), native=()):
    return {'record_kind':'interval', 'time_interval':{'start_game_seconds':a['game_time_seconds'],
        'end_game_seconds':b['game_time_seconds']}, 'skipped_samples_since_previous_valid':0,
        'events':[{'kind':kind,'entity':{'uid':uid}} for kind,uid in states],
        'new_native_events':[dict(e,**IDS,local_player_entity_uid=31) for e in native]}


def pair(a,b, act=None):
    ledger = OperationLedger()
    first = ledger.feed(a)
    assert first['record_kind']=='ledger_baseline'
    return ledger.feed(b, act)


class OperationLedgerTests(unittest.TestCase):
    def test_sole_colonization_payment_matches_residual(self):
        a,b=snap(),snap(11,debit={'Food':40})
        b['operations']['colonizations']=[colony()]
        row=pair(a,b)
        candidate=row['operation_candidates'][0]
        self.assertEqual(candidate['kind'],'colonization_started')
        self.assertEqual(candidate['match_status'],'cost_matches_observed_residual')
        self.assertAlmostEqual(row['resource_links']['Food']['unexplained_residual'],0)
        self.assertEqual(row['resource_links']['Food']['matched_discrete_change'],-40)

    def test_unavailable_extra_cost_never_becomes_zero(self):
        a,b=snap(),snap(11,debit={'Food':40})
        b['operations']['colonizations']=[colony(extra_status='unavailable')]
        b['operations'].update(status='partial',unknown_fields=['colonizations.extra_paid_cost'])
        candidate=pair(a,b)['operation_candidates'][0]
        self.assertIsNone(candidate['cost'])
        self.assertEqual(candidate['match_status'],'unknown_cost')

    def test_contradictory_no_record_cost_is_not_ignored(self):
        a,b=snap(),snap(11,debit={'Food':40})
        row=colony()
        row['extra_paid_cost']=[{'resource':'Food','amount':20}]
        b['operations']['colonizations']=[row]
        self.assertIsNone(pair(a,b)['operation_candidates'][0]['cost'])

    def test_preexisting_paid_record_is_not_new_payment_evidence(self):
        a,b=snap(),snap(11,debit={'Food':40})
        a['operations']['colonizations']=[colony(active=False)]
        b['operations']['colonizations']=[colony()]
        candidate=pair(a,b)['operation_candidates'][0]
        self.assertEqual(candidate['match_status'],'ambiguous')
        self.assertIn('paid_record_preexisted_or_previous_record_unknown',candidate['blockers'])

    def test_stored_building_cost_matches_new_uid_only(self):
        a,b=snap(),snap(11,debit={'Wood':50})
        add_construction(b)
        candidate=pair(a,b,action(a,b,states=[('construction_started_observed',9)]))['operation_candidates'][0]
        self.assertEqual(candidate['match_status'],'cost_matches_observed_residual')
        self.assertEqual(candidate['target']['building_uid'],9)

    def test_missing_stored_building_cost_does_not_guess_static_fee(self):
        a,b=snap(),snap(11,debit={'Wood':50})
        add_construction(b,status='not_stored')
        row=pair(a,b)
        self.assertIsNone(row['operation_candidates'][0]['cost'])
        self.assertAlmostEqual(row['resource_links']['Wood']['unexplained_residual'],-50)

    def test_explicit_free_building_is_a_zero_fee_evidence_branch(self):
        a,b=snap(),snap(11)
        add_construction(b,free=True,status='not_stored')
        row=pair(a,b)
        self.assertEqual(row['operation_candidates'][0]['cost'],[])
        self.assertEqual(row['operation_candidates'][0]['match_status'],'cost_matches_observed_residual')

    def test_mineral_fee_not_claimed_as_calibrated_flow(self):
        a,b=snap(),snap(11,debit={'Wood':50,'Stone':15})
        add_construction(b,cost=[{'resource':'Wood','amount':50},{'resource':'Stone','amount':15}])
        candidate=pair(a,b)['operation_candidates'][0]
        self.assertEqual(candidate['match_status'],'partial_cost_match')
        self.assertEqual(candidate['resource_matches']['Stone']['status'],
            'stock_delta_matches_stored_cost_without_continuous_calibration')

    def test_new_feast_requires_counters_prior_quote_and_success(self):
        a,b=snap(),snap(11,debit={'Food':510})
        b['operations']['feast'].update(feast_count=5,last_feast=10.5)
        b['own_events']=[own_event()]
        candidate=pair(a,b,action(a,b,native=b['own_events']))['operation_candidates'][0]
        self.assertEqual(candidate['kind'],'feast_started')
        self.assertEqual(candidate['match_status'],'cost_matches_observed_residual')

    def test_missing_feast_quote_keeps_unknown_cost(self):
        a,b=snap(),snap(11,debit={'Food':510})
        a['operations']['feast_quote']=None
        b['operations']['feast'].update(feast_count=5,last_feast=10.5)
        b['own_events']=[own_event()]
        self.assertEqual(pair(a,b)['operation_candidates'][0]['match_status'],'unknown_cost')

    def test_feast_success_without_counter_increment_is_ambiguous(self):
        a,b=snap(),snap(11,debit={'Food':510})
        b['operations']['feast']['last_feast']=10.5
        b['own_events']=[own_event()]
        candidate=pair(a,b)['operation_candidates'][0]
        self.assertEqual(candidate['match_status'],'ambiguous')
        self.assertIn('not_exactly_one_observed_feast_counter_increment',candidate['blockers'])

    def test_free_feast_must_use_free_counter_and_zero_prior_quote(self):
        a,b=snap(),snap(11)
        a['operations']['feast_quote'].update(cost=[{'resource':'Food','amount':0}], formula_branch='observed_free_feast')
        a['operations']['feast']['free_feast']=1
        b['operations']['feast'].update(free_feast_count=1,last_feast=10.5)
        b['own_events']=[own_event()]
        self.assertEqual(pair(a,b)['operation_candidates'][0]['match_status'],'cost_matches_observed_residual')

    def test_colonization_completion_never_pays_twice(self):
        a,b=snap(),snap(11)
        a['operations']['colonizations']=[colony()]
        b['operations']['colonizations']=[colony(active=False)]
        b['owned_zone_ids'].append(82)
        candidate=pair(a,b)['operation_candidates'][0]
        self.assertEqual(candidate['kind'],'colonization_completed')
        self.assertIsNone(candidate['cost'])
        self.assertEqual(candidate['match_status'],'not_a_new_payment')

    def test_owned_zone_active_state_does_not_claim_stored_payment(self):
        for previous_active in (None, False):
            with self.subTest(previous_active=previous_active):
                a,b=snap(),snap(11,debit={'Food':40})
                for row in (a,b):
                    row['owned_zone_ids'].append(82)
                if previous_active is not None:
                    a['operations']['colonizations']=[colony(active=previous_active)]
                b['operations']['colonizations']=[colony()]
                row=pair(a,b)
                candidate=row['operation_candidates'][0]
                self.assertEqual(candidate['kind'],'existing_owned_zone_colonization_state')
                self.assertEqual(candidate['payment_effect'],'ownership_progress_change_unknown')
                self.assertIsNone(candidate['cost'])
                self.assertEqual(candidate['resource_matches'],{})
                self.assertEqual(candidate['match_status'],'ambiguous')
                self.assertNotIn('matched_discrete_change',row['resource_links']['Food'])
                self.assertAlmostEqual(row['resource_links']['Food']['unexplained_residual'],-40)

    def test_raven_owned_zone_progress_fixture_start_and_end_are_unknown(self):
        # Minimal recorded fixture, sequences 1065->1066 and 1113->1114:
        # zone 45 was already owned, then colonizeBy=self appeared with progress
        # 1.0; later progress was .983333... before the state disappeared.
        # The ownership never changed, and both payment maps were unavailable.
        cases = (
            (272.2971512464602,272.7980617319803,None,1.0),
            (296.4832767490688,296.9843240902846,.9833333333333345,None),
        )
        for t0,t1,p0,p1 in cases:
            with self.subTest(t0=t0):
                a,b=snap(t0),snap(t1)
                for row,progress in ((a,p0),(b,p1)):
                    row['owned_zone_ids']=[45,55,63]
                    row['discovered_zone_ids']=[45,55,58,63]
                    row['operations'].update(status='partial',
                        unknown_fields=['colonizations.paid_cost','colonizations.extra_paid_cost'])
                    if progress is not None:
                        row['operations']['colonizations']=[{'zone_id':45,
                            'started_by_self':True,'progress':progress,'cost_reduction':None,
                            'paid_cost':None,'paid_cost_record_status':'unavailable',
                            'extra_paid_cost':None,'extra_paid_cost_record_status':'unavailable'}]
                row=pair(a,b)
                self.assertEqual(len(row['operation_candidates']),1)
                candidate=row['operation_candidates'][0]
                self.assertEqual(candidate['kind'],'existing_owned_zone_colonization_state')
                self.assertEqual(candidate['payment_effect'],'ownership_progress_change_unknown')
                self.assertIsNone(candidate['cost'])
                self.assertEqual(candidate['resource_matches'],{})
                self.assertEqual(candidate['match_status'],'ambiguous')
                self.assertNotIn('refund',json.dumps(candidate))

    def test_owned_zone_progress_keeps_operation_identity_guard(self):
        a,b=snap(),snap(11)
        for row in (a,b):
            row['owned_zone_ids'].append(82)
        b['operations']['colonizations']=[colony()]
        b['operations']['capture_run_id']='foreign'
        self.assertEqual(pair(a,b)['operation_candidates'],[])

    def test_construction_completion_is_separate_from_start(self):
        a,b=snap(),snap(11)
        add_construction(a)
        b['buildings']=[{**construction(),'kind':'House','is_construction':False}]
        candidates=pair(a,b,action(a,b,states=[('construction_completed',9)]))['operation_candidates']
        self.assertEqual(len(candidates),1)
        self.assertEqual(candidates[0]['match_status'],'not_a_new_payment')

    def test_cancel_or_disappearance_does_not_invent_refund(self):
        a,b=snap(),snap(11,debit={'Wood':-25})
        add_construction(a)
        row=pair(a,b,action(a,b,states=[('construction_cancelled',9)]))
        self.assertEqual(row['operation_candidates'][0]['match_status'],'ambiguous')
        self.assertAlmostEqual(row['resource_links']['Wood']['unexplained_residual'],25)

    def test_two_operations_not_forced_into_one_exact_sum(self):
        a,b=snap(),snap(11,debit={'Food':40,'Wood':50})
        b['operations']['colonizations']=[colony()]
        add_construction(b)
        for candidate in pair(a,b)['operation_candidates']:
            self.assertEqual(candidate['match_status'],'ambiguous')
            self.assertIn('multiple_potential_payments_in_interval',candidate['blockers'])

    def test_rate_change_remains_uncertain_even_if_numerical_residual_fits(self):
        a,b=snap(),snap(11,debit={'Food':40})
        b['operations']['colonizations']=[colony()]
        a['economy']['resources']['Food'].update(net_per_game_second=.5,gross_aggregate_per_game_second=1.5)
        self.assertEqual(pair(a,b)['operation_candidates'][0]['match_status'],'ambiguous')

    def test_positive_counter_extra_gain_is_not_ignored(self):
        a,b=snap(),snap(11,debit={'Food':40})
        b['operations']['colonizations']=[colony()]
        b['economy']['resources']['Food']['total_produced_counter']+=5
        self.assertEqual(pair(a,b)['operation_candidates'][0]['match_status'],'ambiguous')

    def test_capacity_clip_and_discrete_action_order_is_ambiguous(self):
        a,b=snap(),snap(11,debit={'Food':40})
        b['operations']['colonizations']=[colony()]
        for row in (a,b):
            row['economy']['resources']['Food'].update(stock_cap=600,stock_cap_verified=True,
                stock_cap_source='verified_native_getMax_formula',integration_order='consumption_then_production')
        b['economy']['resources']['Food'].update(stock=560,total_produced_counter=21)
        candidate=pair(a,b)['operation_candidates'][0]
        self.assertEqual(candidate['match_status'],'ambiguous')
        self.assertIn('capacity_and_discrete_action_order_unknown',candidate['blockers'])

    def test_stable_cap_clipping_has_separate_continuous_explanation(self):
        a,b=snap(),snap(11)
        for row in (a,b):
            row['economy']['resources']['Food'].update(stock_cap=600,stock_cap_verified=True,
                stock_cap_source='verified_native_getMax_formula',integration_order='consumption_then_production')
        b['economy']['resources']['Food'].update(stock=600,total_produced_counter=21)
        row=pair(a,b)
        self.assertEqual(row['continuous_economy']['Food']['production_clipped_integral_estimate'],1)
        self.assertAlmostEqual(row['resource_links']['Food']['unexplained_residual'],0)
        self.assertEqual(row['operation_candidates'],[])

    def test_core_race_prevents_matching(self):
        a,b=snap(),snap(11,debit={'Food':40})
        b['operations']['colonizations']=[colony()]
        b['economy']['status']='read_race'
        self.assertEqual(pair(a,b)['operation_candidates'][0]['match_status'],'ambiguous')

    def test_wrong_component_identity_and_clock_refuse_cost_record(self):
        for field,value in [('capture_run_id','foreign'),('time_after',12),('sample_span_game_seconds',1)]:
            with self.subTest(field=field):
                a,b=snap(),snap(11,debit={'Wood':50})
                add_construction(b)
                b['operations'][field]=value
                self.assertEqual(pair(a,b)['operation_candidates'][0]['match_status'],'unknown_cost')

    def test_quote_free_branch_cannot_carry_positive_fee(self):
        a,b=snap(),snap(11,debit={'Food':510})
        a['operations']['feast_quote']['formula_branch']='observed_free_feast'
        b['operations']['feast'].update(free_feast_count=1,last_feast=10.5)
        b['own_events']=[own_event()]
        self.assertEqual(pair(a,b)['operation_candidates'][0]['match_status'],'unknown_cost')

    def test_no_new_cycle_can_still_observe_a_real_debit(self):
        a,b=snap(),snap(10.5,debit={'Food':40})
        b['operations']['colonizations']=[colony()]
        for resource in ('Food','Wood','Money'):
            b['economy']['resources'][resource].update(a['economy']['resources'][resource])
        b['economy']['resources']['Food']['stock']-=40
        self.assertEqual(pair(a,b)['operation_candidates'][0]['match_status'],'cost_matches_observed_residual')

    def test_unexplained_debit_kept_without_guessing_nearby_event(self):
        a,b=snap(),snap(11,debit={'Food':40})
        row=pair(a,b)
        self.assertEqual(row['operation_candidates'],[])
        self.assertEqual(row['resource_links']['Food']['unexplained_residual'],-40)

    def test_cross_session_run_segment_uid_are_new_baselines(self):
        for key in (*IDS,'local_player_entity_uid'):
            with self.subTest(key=key):
                a,b=snap(),snap(11)
                b[key]=32 if key=='local_player_entity_uid' else 'different'
                row=pair(a,b)
                self.assertEqual(row['record_kind'],'ledger_baseline')
                self.assertEqual(row['reason'],'identity_changed')

    def test_invalid_sample_breaks_continuity_and_does_not_bridge(self):
        ledger=OperationLedger();ledger.feed(snap())
        self.assertEqual(ledger.feed({'status':'read_error'})['record_kind'],'ledger_gap')
        self.assertEqual(ledger.feed(snap(11))['record_kind'],'ledger_baseline')

    def test_wrong_action_window_or_native_identity_cannot_match(self):
        a,b=snap(),snap(11,debit={'Food':510})
        b['operations']['feast'].update(feast_count=5,last_feast=10.5)
        b['own_events']=[own_event()]
        act=action(a,b,native=b['own_events']);act['new_native_events'][0]['capture_run_id']='foreign'
        row=pair(a,b,act)
        self.assertEqual(row['operation_candidates'],[])
        act['time_interval']['start_game_seconds']=9
        self.assertEqual(pair(a,b,act)['record_kind'],'ledger_gap')

    def test_sampling_gap_and_rollback_never_join(self):
        self.assertEqual(pair(snap(),snap(20))['record_kind'],'ledger_gap')
        self.assertEqual(pair(snap(),snap(9))['record_kind'],'ledger_baseline')

    def test_pauses_use_game_time_with_no_division_or_wallclock(self):
        row=pair(snap(),snap())
        self.assertEqual(row['record_kind'],'ledger_interval')
        self.assertEqual(row['time_interval']['duration_game_seconds'],0)
        self.assertEqual(row['operation_candidates'],[])

    def test_non_atomic_outer_stock_is_distinct_from_economic_stock(self):
        a,b=snap(),snap(11,debit={'Food':40})
        b['operations']['colonizations']=[colony()]
        b['stocks']['Food']-=.4
        row=pair(a,b)
        self.assertAlmostEqual(row['stock_changes']['Food'],-39.4)
        self.assertAlmostEqual(row['resource_links']['Food']['economic_component_stock_delta'],-39)
        self.assertEqual(row['operation_candidates'][0]['match_status'],'cost_matches_observed_residual')

    def test_input_immutable_and_unknown_fields_never_exported(self):
        a,b=snap(),snap(11,debit={'Wood':50})
        add_construction(b)
        b['pid']=555;b['account_name']='PRIVATE_ACCOUNT';b['ptr']='0xFFFF'
        b['operations']['building_costs'][0]['cost'][0]['pointer']='0xBAD'
        b['buildings'][0]['name']='PRIVATE_BUILDING'
        original=copy.deepcopy((a,b));row=pair(a,b)
        self.assertEqual((a,b),original)
        text=json.dumps(row,allow_nan=False)
        for secret in ('PRIVATE_ACCOUNT','PRIVATE_BUILDING','0xBAD','0xFFFF','pointer','account_name','"pid"'):
            self.assertNotIn(secret,text)

    def test_duplicate_cost_resource_refused(self):
        a,b=snap(),snap(11,debit={'Wood':50})
        add_construction(b,cost=[{'resource':'Wood','amount':25},{'resource':'Wood','amount':25}])
        self.assertIsNone(pair(a,b)['operation_candidates'][0]['cost'])

    def test_constructor_rejects_infinite_matching_tolerance(self):
        with self.assertRaises(ValueError):OperationLedger(absolute_tolerance=float('inf'))
        with self.assertRaises(ValueError):OperationLedger(max_gap=0)


if __name__=='__main__':unittest.main()
