import base64,json,time,urllib.request,urllib.error
from pathlib import Path
values=dict(line.split('=',1) for line in Path('.env').read_text().splitlines() if '=' in line and not line.startswith('#'))
for attempt in range(30):
    try:
        with urllib.request.urlopen('http://127.0.0.1:3001/api/health',timeout=3) as response:identity=json.load(response)
    except (urllib.error.URLError,ValueError):time.sleep(.5);continue
    if identity.get('database')!='ok' or not identity.get('version','').startswith('13.2.4'):
        raise RuntimeError('Unexpected local service; no credentials sent')
    break
else:raise RuntimeError('Grafana readiness timeout; no credentials sent')
headers={'Authorization':'Basic '+base64.b64encode(('admin:'+values['GRAFANA_ADMIN_PASSWORD']).encode()).decode(),'Content-Type':'application/json'}
def grafana(path,method='GET',data=None):
    request=urllib.request.Request('http://127.0.0.1:3001'+path,headers=headers,method=method,data=None if data is None else json.dumps(data).encode())
    with urllib.request.urlopen(request,timeout=30) as response:return json.load(response)
report={'grafana_health':grafana('/api/health'),'datasources':{},'dashboard':None,'queries':[]}
for name in ['prometheus','loki','tempo']:
    report['datasources'][name]=grafana('/api/datasources/uid/ai-team-'+name+'/health')
    assert report['datasources'][name]['status']=='OK'
dashboard=grafana('/api/dashboards/uid/ai-team-operations')['dashboard'];report['dashboard']=dashboard['uid'];report['dashboard_version']=dashboard['version']
assert dashboard['version']>=2
budget_panel=next(p for p in dashboard['panels'] if p['type']=='bargauge')
assert budget_panel['targets'][0].get('instant') is True and budget_panel['targets'][0].get('range') is False
for panel in dashboard['panels']:
    if panel['type']=='text':continue
    target=panel['targets'][0]|{'datasource':panel['datasource'],'intervalMs':15000,'maxDataPoints':200}
    target['expr']=target.get('expr','').replace('$backend','.*').replace('$story_id','.*').replace('$__interval','1m')
    result=grafana('/api/ds/query','POST',{'from':str(int((time.time()-3600)*1000)),'to':str(int(time.time()*1000)),'queries':[target]})
    assert all(not value.get('error') for value in result.get('results',{}).values()),panel['title']
    report['queries'].append({'panel':panel['title'],'successful':True})
report['prometheus_targets']=grafana('/api/datasources/proxy/uid/ai-team-prometheus/api/v1/targets')['data']['activeTargets']
report['prometheus_targets']=[{'job':t['labels']['job'],'health':t['health']} for t in report['prometheus_targets']]
assert all(t['health']=='up' for t in report['prometheus_targets'])
Path('docs/stage4-stack-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False))
