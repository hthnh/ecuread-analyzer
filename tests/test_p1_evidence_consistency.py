from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.integration.diagnostic_evidence import aggregate_from_detectors, normalize_detector
from app.ml.evidence_aggregation import summarize_aggregate_windows
from tests.test_evidence_aggregation import _finding, _window


def test_three_detectors_preserve_overlap_disagreement_and_partial_coverage():
    rows = [_window(0, [_finding('if', 'positive'), _finding('battery', 'positive'), _finding('speed', 'negative')]),
            _window(1, [_finding('if', 'negative'), _finding('battery', 'not_applicable'), _finding('speed', 'negative')]),
            _window(2, [_finding('if', 'negative'), _finding('battery', 'negative'), _finding('speed', 'negative')])]
    summary = summarize_aggregate_windows(rows)
    assert summary['coverage_status_counts'] == {'full': 2, 'partial': 1, 'none': 0}
    assert summary['overlapping_finding_windows'] == summary['disagreement_windows'] == 1
    assert summary['all_applicable_agreement_windows'] == summary['both_applicable_agreement_windows'] == 1
    detectors = {name: normalize_detector({'detector_id': name, 'status': 'ok', 'scored_window_count': 3, 'anomaly_window_count': 1}) for name in ('if','battery','speed')}
    aggregate = aggregate_from_detectors(detectors, summary)
    assert aggregate['session_detector_summary']['evidence_state'] == 'multiple_detector_evidence'
    assert aggregate['coverage_status'] == 'partial'
    assert aggregate['evidence_state'] == 'detector_disagreement'
    assert aggregate['detector_disagreement'] == {'scope': 'window_presence_within_session', 'present': True,
        'disagreement_window_count': 1, 'positive_detector_ids': ['battery', 'if'], 'negative_detector_ids': ['speed'], 'participant_ids_available': True}
    assert len(rows[0]['detector_findings']) == 3


@pytest.mark.parametrize('fixture,expected_windows,expected_full,expected_partial,expected_disagreement', [
    ('data/analyses/analysis_20261012T133148Z_9807d0063a69/analysis_run.json',4,3,1,3),
    ('data/evaluation/long_session_compatibility/smoke_report.json',1793,1785,8,117),
])
def test_stored_regression_scope_without_rescoring(fixture,expected_windows,expected_full,expected_partial,expected_disagreement):
    source = json.loads(Path(fixture).read_text())
    evidence = source.get('analysis',source)['diagnostic_evidence']
    before = deepcopy(evidence)
    summary = evidence['aggregate']['window_summary']
    aggregate = aggregate_from_detectors(evidence['detectors'],summary)
    assert aggregate['window_coverage'] == {'window_count':expected_windows,'full':expected_full,'partial':expected_partial,'none':0}
    assert aggregate['coverage_status'] == 'partial'
    assert aggregate['detector_disagreement']['present'] is True
    assert aggregate['detector_disagreement']['disagreement_window_count'] == expected_disagreement
    assert aggregate['evidence_state'] == 'detector_disagreement'
    assert evidence == before


def test_execution_is_not_applicability_and_no_windows_is_no_coverage():
    detectors = {name:normalize_detector({'detector_id':name,'status':status,'scored_window_count':count,'anomaly_window_count':0})
        for name,status,count in [('executed','ok',0),('skipped','skipped',0),('failed','failed',0)]}
    aggregate = aggregate_from_detectors(detectors, summarize_aggregate_windows([]))
    assert aggregate['counts']['executed_detector_count'] == 1
    assert aggregate['counts']['applicable_detector_count'] == 0
    assert aggregate['coverage_status'] == 'none'
    assert aggregate['evidence_state'] == 'insufficient_coverage'
    assert aggregate['detector_disagreement']['present'] is False
