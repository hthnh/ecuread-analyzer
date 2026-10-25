from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.ml.root_cause_analysis_v2 import RootCauseAnalyzerV2
from app.ml.evidence_aggregation import build_aggregate_events, representative_events
from tests.p2_evidence_helpers import replay
from tests.test_root_cause_analysis_v2 import _case


@pytest.fixture
def packet():
    return json.loads((Path(__file__).parent/'fixtures/p2_canary_windows.json').read_text())


def test_real_voltage_values_and_references_survive_without_a_cause(packet):
    original = deepcopy(packet)
    output = replay(packet)
    item = next(o for o in output['interpretation']['observations'] if o['observation_id']=='obs.v2.contextual_voltage_deviation')
    assert item['measurements'][0]['value']==10.7
    assert item['reference_values'][0]['value']==14.1
    assert item['reference_values'][0]['manufacturer_specification'] is False
    assert item['deviation']['difference']==pytest.approx(-3.4)
    assert item['deviation']['robust_z']==pytest.approx(5.0962222222)
    assert item['operating_context']=='idle_closed_throttle'
    assert item['event_reference']['representative_window']['start_time_ms']==0
    assert item['additional_evidence_needed']==['external_voltage_measurement','physical_electrical_inspection']
    assert output['interpretation']['possible_causes']==[]
    assert packet==original


def test_real_speed_uses_actual_values_without_inventing_reference_medians(packet):
    output = replay(packet)
    item = next(o for o in output['interpretation']['observations'] if 'contextual_speed' in o.get('supporting_detector_ids',[]))
    assert {'speed','rpm','throttle'}==set(item['affected_signals'])
    assert item['reference_values']==[]
    assert item['deviation']['robust_deviations']
    assert any(m['feature']=='speed_median' and m['unit']=='km/h' for m in item['measurements'])
    assert item['claim_level']=='observation'
    assert 'p2.limit.speed' in item['limiting_evidence']


def test_overlapping_windows_keep_one_group_and_all_evidence_references(packet):
    output = replay(packet)
    event = next(e for e in output['events']['finding_events'] if e['window_count']>1)
    assert len(event['window_evidence_refs'])==event['window_count']
    assert event['start_time_ms']<=event['end_time_ms']
    assert event['source_window_range']['end_window_number']-event['source_window_range']['start_window_number']+1==event['window_count']
    assert sum(event['window_coverage'].values())==event['window_count']
    # Peak witness selection is within a detector, not across its score scale.
    witness=event['detector_representatives']['isolation_forest']
    candidates=[row for row in output['rows'] if event['start_window_number']<=row['window_number']<=event['end_window_number']]
    expected=min(next(f['detector_local_score']['value'] for f in row['detector_findings'] if f['detector_id']=='isolation_forest') for row in candidates)
    actual=next(f['detector_local_score']['value'] for f in witness['detector_findings'] if f['detector_id']=='isolation_forest')
    assert actual==expected


def test_if_feature_involvement_never_becomes_a_component_claim(packet):
    out=replay(packet)
    item=next(o for o in out['interpretation']['observations'] if o['observation_id']=='obs.v2.iforest_outside_reference')
    assert item['reference_values']==[] and item['claim_level']=='observation'
    assert 'p2.limit.if' in item['limiting_evidence']
    assert out['interpretation']['possible_causes']==[]
    assert all(not symptom['symptom_id'].startswith('sym.v2.iforest') for symptom in out['interpretation']['symptoms'])


def test_check_priorities_link_to_observations_and_keep_all_event_sources(packet):
    out=replay(packet);checks=out['interpretation']['recommended_checks']
    assert len({c['check_id'] for c in checks})==len(checks)
    voltage=next(c for c in checks if c['check_id']=='check.v2.external_supply_measurement')
    assert voltage['diagnostic_priority']=='first' and voltage['supporting_finding_ids']
    assert voltage['provides']['key']=='p2.provides.voltage'
    assert any(c['check_id']=='check.v2.speed_context_review' for c in checks)
    assert all(c.get('rationale') for c in checks)


def test_event_selection_does_not_crowd_out_rare_contextual_evidence(packet):
    out=replay(packet);early=next(e for e in out['events']['finding_events'] if e['positive_detector_ids']==['isolation_forest'])
    rare=next(e for e in out['events']['finding_events'] if 'contextual_speed' in e['positive_detector_ids'])
    copies=[]
    for i in range(12):
        event=deepcopy(early);event['event_id']=f'early-{i}';copies.append(event)
    selected=representative_events({'finding_events':copies+[rare]},limit_per_type=5)
    assert rare in selected


@pytest.mark.parametrize('state',['negative','unavailable'])
def test_negative_or_unavailable_results_do_not_invent_positive_observations(packet,state):
    p=deepcopy(packet)
    for item in p['items']:
        for detector in item['window']['detector_results']:
            detector['is_anomaly']=False if state=='negative' else None
            if state=='unavailable':detector['status']='skipped'
    # A no-finding case follows the existing summary fallback in the live harness.
    from app.services.research_harness import _summary_events_for_rca, _rca_case
    from app.domain.telemetry import CanonicalTelemetrySession
    rows=replay(p)['rows'];event=_summary_events_for_rca(p['source_session'],rows)[0]
    case=_rca_case(analysis_run_id='negative',session=CanonicalTelemetrySession(decoder_id='honda_keihin_71_17',decoder_version='1.0.0',samples=[]),event=event,index=1,signal_columns=[],telemetry_schema_version='canonical-telemetry-v2')
    out=RootCauseAnalyzerV2().analyze_case(case)
    assert out['symptoms']==[] and out['possible_causes']==[]
    assert not any(o.get('supporting_detector_ids') for o in out['observations'])


def test_causal_gates_stay_closed_without_existing_additional_evidence():
    base=_case(contextual_finding='positive',contextual_top_deviation={'feature':'battery_voltage_min','observed':10.7,'reference_median':14.1})
    assert RootCauseAnalyzerV2().analyze_case(base)['possible_causes']==[]
    base['additional_causal_evidence']={'external_voltage_measurement':{'voltage':10.6,'source':'physical-measurement-fixture'}}
    causes=RootCauseAnalyzerV2().analyze_case(base)['possible_causes']
    assert len(causes)==1 and causes[0]['maximum_claim_level']=='possible_hypothesis'


def test_different_detector_contexts_and_frame_gaps_stay_separate(packet):
    row=replay(packet)['rows'][1]
    other=deepcopy(row);other['window_number']=row['window_number']+1
    other['start_frame_index']=row['end_frame_index']+100
    assert len(build_aggregate_events([row,other])['finding_events'])==2


def test_missing_times_do_not_sum_overlapping_window_durations(packet):
    rows=deepcopy(replay(packet)['rows'][1:3])
    for row in rows:row['start_time_ms']=row['end_time_ms']=None
    event=build_aggregate_events(rows)['finding_events'][0]
    assert event['window_count']==2 and event['duration_ms'] is None


def test_if_context_belongs_to_its_own_witness_not_another_detectors_peak(packet):
    p=deepcopy(packet);p['items']=p['items'][1:3]
    for i,item in enumerate(p['items']):
        for detector in item['window']['detector_results']:
            if detector['detector_id']=='contextual_battery_voltage':
                detector['evidence']['contextual_evidence']['operating_context']='idle_closed_throttle' if i==0 else 'mid_load_running'
                detector['anomaly_score']=1.0 if i==0 else 3.0
            if detector['detector_id']=='isolation_forest':detector['anomaly_score']=-0.8 if i==0 else -0.6
    out=replay(p)
    item=next(o for o in out['interpretation']['observations'] if o['observation_id']=='obs.v2.iforest_outside_reference')
    assert item['operating_context']=='idle_closed_throttle'
