"""Build current working trees and verify P1 without joining production networks.

Usage: python scripts/verify_p1_staging.py --work-dir /tmp/ecuread-p1-<unique> --phase prepare|verify|cleanup
Only the explicitly named disposable project is started/stopped. Production is read-only fingerprinted.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import select
import socket
import socketserver
from threading import Thread
from time import sleep

ROOT=Path(__file__).resolve().parents[1]
DRIVESAFE=ROOT.parent/'drisafe'
PRODUCTION=('ecu-analyzer','drisafe-web','drisafe-db-1','drisafe-redis-1')


def command(args,**kwargs):
    return subprocess.run(args,check=True,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,**kwargs).stdout


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def file_manifest(directory):
    return {str(path.relative_to(directory)):digest(path) for path in sorted(directory.rglob('*')) if path.is_file() and '__pycache__' not in path.parts and path.suffix!='.pyc'}


def production_snapshot():
    details=json.loads(command(['docker','inspect',*PRODUCTION]))
    containers={item['Name'].lstrip('/'):{'id':item['Id'],'image':item['Image'],'started_at':item['State']['StartedAt'],
        'mounts':item['Mounts'],'networks':sorted(item['NetworkSettings']['Networks']),
        'state':item['State']['Status']} for item in details}
    data={}
    for name in ('models','telemetry','analyses','entities'):
        for relative,value in file_manifest(ROOT/'data'/name).items():data[name+'/'+relative]=value
    tables={}
    for table in ('ride_sessions','telemetry_records','analysis_results','sync_batches'):
        sql=f'BEGIN READ ONLY; COPY (SELECT row_to_json(t) FROM {table} t ORDER BY id) TO STDOUT; COMMIT;'
        process=subprocess.Popen(['docker','exec','drisafe-db-1','sh','-c','exec psql -X -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"','p1-readonly',sql],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        h=hashlib.sha256();size=0
        while chunk:=process.stdout.read(1024*1024):h.update(chunk);size+=len(chunk)
        error=process.stderr.read();assert process.wait()==0,error.decode()
        tables[table]={'sha256':h.hexdigest(),'bytes':size}
    return {'containers':containers,'analyzer_data':data,'database_tables':tables}


def runtime(compose,service,packages):
    script='import json,sys;from importlib.metadata import version;print(json.dumps({"python":sys.version,"packages":{p:version(p) for p in '+repr(packages)+'}}))'
    return json.loads(command(compose+['exec','-T',service,'python','-c',script]))


def memory(compose,service):
    return json.loads(command(compose+['exec','-T',service,'python','-c',
        'import json,pathlib;print(json.dumps({key:int(pathlib.Path("/sys/fs/cgroup/"+key).read_text()) for key in ("memory.current","memory.peak","memory.max")}))']))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--work-dir',type=Path,required=True);parser.add_argument('--phase',choices=('prepare','verify','browser','cleanup'),required=True)
    args=parser.parse_args();work=args.work_dir.resolve()
    assert work.parent==Path('/tmp') and work.name.startswith('ecuread-p1-')
    work.mkdir(exist_ok=True);work.chmod(0o755)
    project=work.name.replace('_','-').lower()
    compose=['docker','compose','-p',project,'-f',str(work/'compose.json')]
    if args.phase=='browser':
        # Docker internal bridges do not publish the configured host ports on
        # this engine. A temporary loopback relay permits a real browser visit.
        proxy=json.loads(command(['docker','inspect',command(compose+['ps','-q','proxy']).strip()]))[0]
        assert set(proxy['NetworkSettings']['Networks'])=={project+'_default'}
        address=proxy['NetworkSettings']['Networks'][project+'_default']['IPAddress']
        class Relay(socketserver.BaseRequestHandler):
            def handle(self):
                with socket.create_connection((address,80),timeout=90) as upstream:
                    peers=(self.request,upstream)
                    while True:
                        ready,_,_=select.select(peers,[],[],90)
                        if not ready:return
                        for peer in ready:
                            data=peer.recv(65536)
                            if not data:return
                            (upstream if peer is self.request else self.request).sendall(data)
        class Server(socketserver.ThreadingTCPServer):
            daemon_threads=True
        with Server(('127.0.0.1',0),Relay) as relay:
            thread=Thread(target=relay.serve_forever,daemon=True);thread.start()
            try:
                print(command(['node',str(DRIVESAFE/'scripts/verify_p1_browser.cjs'),f'http://127.0.0.1:{relay.server_address[1]}',str(ROOT/'docs/integration')]),flush=True)
            finally:relay.shutdown();thread.join()
        return
    if args.phase=='cleanup':
        print(command(compose+['down','--volumes','--remove-orphans']))
        return
    if args.phase=='prepare':
        before=production_snapshot();(work/'production-before.json').write_text(json.dumps(before,indent=2))
        # Snapshot actual source files, retaining uncommitted and untracked implementation.
        # Analyzer Dockerfile copies exactly these directories, never data or secrets.
        source=work/'analyzer-source';source.mkdir(exist_ok=True)
        for name in ('app','scripts'):
            shutil.copytree(ROOT/name,source/name,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        for name in ('Dockerfile','pyproject.toml','README.md'):shutil.copyfile(ROOT/name,source/name)
        websource=work/'web-source'
        shutil.copytree(DRIVESAFE,websource,dirs_exist_ok=True,ignore=shutil.ignore_patterns('.git','.venv','__pycache__','*.pyc','.env','.env.*','*.sqlite','*.db','*.bak','*.old','*.copy','*.tmp','backups','artifacts','node_modules','.pytest_cache','.mypy_cache','.ruff_cache','htmlcov'))
        # Keep only copied, active model artifacts and read-only descriptive history inputs.
        sys.path.insert(0,str(ROOT/'scripts'))
        from verify_large_raw_sessions import copy_active_artifacts
        protected=copy_active_artifacts(work/'analyzer-data')
        for path in (work/'analyzer-data').rglob('*'):
            path.chmod(0o777 if path.is_dir() else 0o666)
        (work/'analyzer-data').chmod(0o777)
        verification=work/'verification';verification.mkdir(exist_ok=True);verification.chmod(0o755)
        shutil.copyfile(ROOT/'real_data/fulldata_afterclean/session_000018.jsonl',verification/'input.jsonl')
        (work/'output').mkdir(exist_ok=True);(work/'output').chmod(0o777)
        proxy_image=json.loads(command(['docker','inspect','nginx-proxy-manager']))[0]['Image']
        db_image=before['containers']['drisafe-db-1']['image'];redis_image=before['containers']['drisafe-redis-1']['image']
        config='''user root;
worker_processes 1;
pid /tmp/nginx.pid;
error_log /dev/stderr notice;
events { worker_connections 256; }
http { access_log /dev/stdout; client_body_temp_path /tmp/client_body;
 proxy_temp_path /tmp/proxy_temp; fastcgi_temp_path /tmp/fastcgi_temp; uwsgi_temp_path /tmp/uwsgi_temp; scgi_temp_path /tmp/scgi_temp;
 server { listen 80; client_max_body_size 2000m;
 location / { proxy_pass http://web:5000; proxy_connect_timeout 90s; proxy_send_timeout 90s; proxy_read_timeout 90s; proxy_set_header Host $host; }
 } }
'''
        (work/'nginx.conf').write_text(config)
        definition={'services':{
            'analyzer':{'image':project+'-analyzer:candidate','build':{'context':str(source)},'environment':{'DATA_DIR':'/app/data','MODEL_DIR':'/app/data/models','ALLOW_MODEL_TRAINING_API':'false','WINDOW_SIZE_SAMPLES':'50','WINDOW_STEP_SAMPLES':'10'},'volumes':[str(work/'analyzer-data')+':/app/data'],'ports':['127.0.0.1::8000'],'mem_limit':'1g','cpus':1.5},
            'db':{'image':db_image,'environment':{'POSTGRES_DB':'p1_harness','POSTGRES_USER':'p1','POSTGRES_PASSWORD':'disposable-p1-only'},'volumes':['p1_postgres:/var/lib/postgresql/data'],'mem_limit':'256m','cpus':.5,'healthcheck':{'test':['CMD-SHELL','pg_isready -U p1 -d p1_harness'],'interval':'2s','timeout':'3s','retries':30}},
            'redis':{'image':redis_image,'command':['redis-server','--appendonly','no','--save',''],'mem_limit':'128m','cpus':.25},
            'web':{'image':project+'-web:candidate','build':{'context':str(websource)},'environment':{
                'APP_ENV':'production','SECRET_KEY':'disposable-p1-secret-not-production','DB_HOST':'db','POSTGRES_DB':'p1_harness','POSTGRES_USER':'p1','POSTGRES_PASSWORD':'disposable-p1-only',
                'SESSION_COOKIE_SECURE':'false','TRUST_PROXY':'false','TRUSTED_HOSTS':'proxy,127.0.0.1,localhost,web',
                'REDIS_URL':'redis://redis:6379/0','LIVE_REDIS_REQUIRED':'true','ML_API_BASE_URL':'http://analyzer:8000','ML_API_TIMEOUT_SECONDS':'60',
                'MAX_LOG_RECORDS':'1000','MAX_CONTENT_LENGTH':'2097152','P1_DISPOSABLE_STAGING':'true'},
                'depends_on':{'db':{'condition':'service_healthy'}},'volumes':[str(verification)+':/verification:ro',str(work/'output')+':/verification-output'],'mem_limit':'768m','cpus':1},
            'proxy':{'image':proxy_image,'entrypoint':['nginx'],'command':['-e','/dev/stderr','-c','/etc/nginx/p1.conf','-g','daemon off;'],'volumes':[str(work/'nginx.conf')+':/etc/nginx/p1.conf:ro'],'ports':['127.0.0.1::80'],'depends_on':['web'],'mem_limit':'128m','cpus':.25}},
            'networks':{'default':{'internal':True}},'volumes':{'p1_postgres':{}}}
        (work/'compose.json').write_text(json.dumps(definition,indent=2))
        (work/'source-manifest.json').write_text(json.dumps({'analyzer':file_manifest(source),'drivesafe':file_manifest(websource)},indent=2))
        (work/'protected-artifacts.json').write_text(json.dumps(protected,indent=2))
        print('Building candidate images from source snapshots.',flush=True)
        for service in ('analyzer','web'):
            with (work/f'build-{service}.log').open('w') as log:
                subprocess.run(compose+['build',service],check=True,stdout=log,stderr=subprocess.STDOUT)
            print('Built '+service,flush=True)
        print(command(compose+['up','-d']),flush=True)
        for _ in range(40):
            try:
                command(compose+['exec','-T','web','python','-c','import urllib.request;urllib.request.urlopen("http://proxy/auth/login",timeout=2).read();urllib.request.urlopen("http://analyzer:8000/health",timeout=2).read()'])
                break
            except subprocess.CalledProcessError:sleep(1)
        else:raise RuntimeError('Disposable staging did not become ready; inspect compose logs.')
        print(json.dumps({'status':'ready','work_dir':str(work),'project':project}),flush=True)
        return
    # Runtime assertion: staging has only its private network and disposable mounts.
    ids=command(compose+['ps','-q']).split()
    inspected=json.loads(command(['docker','inspect',*ids]))
    production_mounts={mount['Source'] for container in production_snapshot()['containers'].values() for mount in container['mounts']}
    for item in inspected:
        assert set(item['NetworkSettings']['Networks'])=={project+'_default'}
        assert not any(mount['Source'] in production_mounts for mount in item['Mounts'])
    before=json.loads((work/'production-before.json').read_text())
    runtimes={'analyzer':runtime(compose,'analyzer',['fastapi','uvicorn','numpy','pandas','scikit-learn','joblib','pydantic']),
              'drivesafe':runtime(compose,'web',['Flask','SQLAlchemy','psycopg','gunicorn','requests'])}
    baseline_memory={service:memory(compose,service) for service in ('analyzer','web')}
    print(command(compose+['exec','-T','web','python','scripts/verify_p1_http.py','--input','/verification/input.jsonl','--output','/verification-output/http.json']),flush=True)
    peaks={service:memory(compose,service) for service in ('analyzer','web')}
    result=json.loads((work/'output/http.json').read_text());run_id=result['analysis_run_id']
    run=json.loads((work/'analyzer-data/analyses'/run_id/'analysis_run.json').read_text())
    windows=[json.loads(line) for line in (work/'analyzer-data/analyses'/run_id/'analysis_windows.jsonl').read_text().splitlines()]
    assert run['diagnostic_evidence']==result['diagnostic_evidence']
    assert run['anomaly_timeline']==result['primary_timeline']
    assert len(windows)==1258 and all(len(window['detector_results'])==3 for window in windows)
    assert [(window['start_sequence'],window['end_sequence']) for window in windows]==[(i,i+49) for i in range(0,12625-49,10)]
    assert len(list((work/'analyzer-data/analyses').glob('*/analysis_run.json')))==3, 'Retries must reuse the same Analyzer result.'
    protected=json.loads((work/'protected-artifacts.json').read_text())
    for original,sha in protected.items():
        assert digest(original)==sha
        assert digest(work/'analyzer-data'/Path(original).relative_to(ROOT/'data'))==sha
    for relative,sha in file_manifest(work/'analyzer-source').items():assert digest(ROOT/relative)==sha
    web_manifest=json.loads((work/'source-manifest.json').read_text())['drivesafe']
    for relative,sha in web_manifest.items():assert digest(DRIVESAFE/relative)==sha
    after=production_snapshot();(work/'production-after.json').write_text(json.dumps(after,indent=2))
    assert before==after,'Production read-only fingerprints changed; inspect before/after.'
    report={'status':'passed','project':project,'runtime':runtimes,'memory_scope':'Whole container cgroup v2 lifetime peak, including startup and verifier process; no peak reset.',
            'memory_baseline':baseline_memory,'memory_after_acceptance':peaks,
            'production_containers_unchanged':before['containers']==after['containers'],
            'production_database_hashes_before_after':before['database_tables'],'production_analyzer_protected_file_count':len(before['analyzer_data']),
            'production_analyzer_files_unchanged':True,'active_artifact_sha256':protected,'source_manifest':json.loads((work/'source-manifest.json').read_text()),
            'staging_containers':[{ 'name':i['Name'],'image':i['Image'],'networks':sorted(i['NetworkSettings']['Networks']),'mounts':i['Mounts'],'ports':i['NetworkSettings']['Ports']} for i in inspected],
            'http_acceptance':result,'persisted_windows_verified':1258,'distinct_analyzer_runs_for_three_logical_inputs':3}
    (ROOT/'docs/integration/P1_STAGING_REGRESSION_2026_10_23.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'status':'passed','http_seconds':result['full_http_seconds'],'peak_memory_mib':{k:round(v['memory.peak']/1024**2,2) for k,v in peaks.items()},'production_unchanged':True}),flush=True)


if __name__=='__main__':
    try:main()
    except subprocess.CalledProcessError as exc:
        print((exc.stderr or '')[-4000:],file=sys.stderr);raise
