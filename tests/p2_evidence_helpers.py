"""Replay stored detector evidence without running ML; shared by P2 regressions."""
from copy import deepcopy

from app.domain.telemetry import CanonicalTelemetrySession
from app.ml.evidence_aggregation import aggregate_window, build_aggregate_events, representative_events, AggregationPolicy
from app.services.research_harness import _run_rca_v2, _interpretation_from_rca


def replay(packet):
    rows = []
    for item in packet['items']:
        window = item['window']
        findings = []
        for detector in window['detector_results']:
            identity = detector['detector_id']
            applicable = detector['status'] == 'ok' and detector.get('is_anomaly') is not None
            evidence = deepcopy(detector.get('evidence') or {})
            finding = 'positive' if applicable and detector['is_anomaly'] else 'negative' if applicable else 'not_applicable' if detector['status'] == 'ok' else 'unavailable'
            findings.append({'detector_id': identity, 'model_version': detector['model_version'], 'execution_status': detector['status'],
                'available': detector['status'] == 'ok', 'applicable': applicable, 'finding': finding, 'prediction': detector.get('prediction'),
                'is_anomaly': detector.get('is_anomaly'), 'evidence': evidence, 'missing_features': [], 'error': None,
                'detector_local_score': {'value': detector.get('anomaly_score'), 'direction': 'lower_is_more_anomalous' if identity == 'isolation_forest' else 'higher_is_more_anomalous'},
                'detector_local_threshold': {'value': 0 if identity == 'isolation_forest' else evidence.get('contextual_threshold', evidence.get('speed_threshold'))}})
        rows.append(aggregate_window(session_id=packet['source_session'], window_identity={
            'window_number': window['window_index'], 'window_index': window['window_index'],
            'start_frame_index': window['start_sequence'], 'end_frame_index': window['end_sequence'],
            'start_time_ms': item.get('start_time_ms'), 'end_time_ms': item.get('end_time_ms'), 'duration_ms': window['duration_ms']},
            detector_findings=findings, telemetry=deepcopy(item['features']), policy=AggregationPolicy(detector_ids=tuple(d['detector_id'] for d in findings))))
    events = build_aggregate_events(rows)
    selected = representative_events(events, limit_per_type=5)
    session = CanonicalTelemetrySession(session_id=packet['source_session'], decoder_id='honda_keihin_71_17', decoder_version='1.0.0',
        telemetry_schema_version='canonical-telemetry-v2', samples=[packet['provenance_sample']] if packet.get('provenance_sample') else [])
    results = _run_rca_v2(analysis_run_id=packet['source_run'], session=session, events=selected,
        signal_columns=['rpm','tps_voltage','tps_raw','battery_voltage','iat_c','ect_c'], telemetry_schema_version='canonical-telemetry-v2')
    return {'rows': rows, 'events': events, 'selected': selected, 'results': results, 'interpretation': _interpretation_from_rca(results)}
