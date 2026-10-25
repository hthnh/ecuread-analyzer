"""Interpret COPIES of frozen P1.5 evidence, without inference or production writes.

The work directory contains canary-copy/{analysis_run.json,analysis_windows.jsonl,
window_features.jsonl} and canonical-copy/samples.jsonl copied before development.
"""
from copy import deepcopy
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tests.p2_evidence_helpers import replay


def clean(value):
    if isinstance(value,float) and not math.isfinite(value):return None
    if isinstance(value,dict):return {k:clean(v) for k,v in value.items()}
    if isinstance(value,list):return [clean(v) for v in value]
    return value


def main():
    p=argparse.ArgumentParser();p.add_argument('--work-dir',type=Path,required=True);p.add_argument('--export-consumer-fixture',type=Path)
    args=p.parse_args();work=args.work_dir.resolve()
    assert work.parent==Path('/tmp') and work.name.startswith('ecuread-p2-'),'Use disposable copied evidence only'
    source=json.loads((work/'canary-copy/analysis_run.json').read_text())['request_summary']
    windows=[json.loads(s) for s in (work/'canary-copy/analysis_windows.jsonl').read_text().splitlines()]
    features=[json.loads(s)['features_json'] for s in (work/'canary-copy/window_features.jsonl').read_text().splitlines()]
    samples={s['sequence']:s for s in map(json.loads,(work/'canonical-copy/samples.jsonl').read_text().splitlines())}
    packet={'source_run':source['analysis_run_id'],'source_session':source['session_id'],'provenance_sample':samples[0],
        'items':[{'window':row,'features':features[i],'start_time_ms':samples[row['start_sequence']]['timestamp_ms'],'end_time_ms':samples[row['end_sequence']]['timestamp_ms']} for i,row in enumerate(windows)]}
    digest=lambda:hashlib.sha256(json.dumps(packet,sort_keys=True).encode()).hexdigest()
    before=digest();output=replay(packet);assert digest()==before
    preview=deepcopy(source)
    preview['diagnostic_evidence']['interpretation']=output['interpretation']
    preview['diagnostic_evidence']['events']['items']=output['selected']
    # History remains descriptive; do not invent or reuse priorities for newly added checks.
    preview['diagnostic_evidence']['recommended_check_priorities']=[]
    for key in source:
        if key!='diagnostic_evidence':assert preview[key]==source[key]
    for key in ['aggregate','detectors']:assert preview['diagnostic_evidence'][key]==source['diagnostic_evidence'][key]
    (work/'canary-p2-preview.json').write_text(json.dumps(clean(preview),indent=2)+'\n')
    acceptance={'status':'passed','source_run':source['analysis_run_id'],'records':len(samples),'windows':len(windows),
        'inference_executed':False,'original_window_payload_sha256':before,'original_inputs_unchanged':True,'prediction_health_detector_and_aggregation_fields_unchanged':True,
        'grouped_finding_events':len(output['events']['finding_events']),'reviewed_events':len(output['selected']),
        'interpretation_counts':{k:len(output['interpretation'][k]) for k in ['observations','symptoms','possible_causes','recommended_checks']},
        'concrete_message_keys':sorted({o['presentation']['key'] for o in output['interpretation']['observations'] if o.get('presentation')}),
        'preview_bytes':(work/'canary-p2-preview.json').stat().st_size}
    (work/'stored-evidence-acceptance.json').write_text(json.dumps(acceptance,indent=2)+'\n')
    if args.export_consumer_fixture:
        bounded=json.loads((ROOT/'tests/fixtures/p2_canary_windows.json').read_text());result=replay(bounded)
        fixture={'analysis_run_id':source['analysis_run_id'],'model_version':source['model_version'],'diagnostic_evidence':{
            'schema_version':source['diagnostic_evidence']['schema_version'],'interpretation':result['interpretation'],
            'events':{'items':result['selected']},'aggregate':source['diagnostic_evidence']['aggregate'],
            'detectors':{key:{k:v for k,v in d.items() if k!='evidence'} for key,d in source['diagnostic_evidence']['detectors'].items()}}}
        args.export_consumer_fixture.write_text(json.dumps(clean(fixture),separators=(',',':'))+'\n')
    print(json.dumps(acceptance))


if __name__=='__main__':main()
