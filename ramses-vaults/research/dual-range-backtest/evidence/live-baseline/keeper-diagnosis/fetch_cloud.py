"""Read-only: stores deployment/app metadata for the LP keeper with env KEY NAMES only (no values)."""
import json,subprocess
A='6dc249f8-8e9d-43d0-9ba1-fcbaaa6b5e9c'
def d(*a):
    r=subprocess.run(['doctl','--context','peridot-keeper-database',*a],capture_output=True,text=True,timeout=170)
    if r.returncode: raise SystemExit('doctl failed '+' '.join(a)+': '+r.stderr[:300])
    return r.stdout
dep=json.loads(d('apps','list-deployments',A,'-o','json'))
out=[]
for x in dep:
    workers=x.get('spec',{}).get('workers',[])
    r=dict(id=x['id'],phase=x.get('phase'),cause=x.get('cause'),created=x.get('created_at'),updated=x.get('updated_at'),phase_last=x.get('phase_last_updated_at'),
      cause_details=x.get('cause_details'),worker_src=[{'name':w.get('name'),'source_commit':w.get('source_commit_hash')} for w in x.get('workers',[])],
      git=[w.get('git') or w.get('github') for w in workers],envKeys=[[(e.get('key'),e.get('type')) for e in w.get('envs',[])] for w in workers],tier=x.get('tier_slug'),
      steps=[(s.get('name'),s.get('status'),s.get('started_at'),s.get('ended_at')) for s in (x.get('progress') or {}).get('summary_steps',[]) or []])
    # the only non-secret flag values needed; never read SECRET values
    r['generalFlags']=[{e['key']:e.get('value') for e in w.get('envs',[]) if e.get('type')=='GENERAL' and e.get('key') in('EXECUTE','INTERVAL_SECONDS')} for w in workers]
    out.append(r)
    print(r['created'],r['id'][:8],r['phase'],r['cause'],r['worker_src'],r['generalFlags'],[k for k,_ in (r['envKeys'][0] if r['envKeys'] else [])])
json.dump(out,open('deployments.json','w'),indent=1)
a=json.loads(d('apps','get',A,'-o','json')); a=a[0] if isinstance(a,list) else a
s=a['spec']; w=s['workers'][0]; ad=a.get('active_deployment',{})
sm=dict(id=a['id'],created_at=a['created_at'],updated_at=a['updated_at'],last_deployment_created_at=a.get('last_deployment_created_at'),tier=a.get('tier_slug'),region=(a.get('region') or {}).get('slug'),active=dict(id=ad.get('id'),phase=ad.get('phase'),cause=ad.get('cause'),created_at=ad.get('created_at'),workers=ad.get('workers')),workerNoEnv={k:v for k,v in w.items() if k!='envs'},envKeysOnly=[(e['key'],e.get('type')) for e in w.get('envs',[])],alerts=s.get('alerts'),keys=sorted(a))
json.dump(sm,open('app-summary.json','w'),indent=1); print(json.dumps(sm,indent=1))
