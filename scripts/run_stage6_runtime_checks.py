"""Targeted verification of persistent restart policy and bounded trusted gateway."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import product_operator as host

def main():
    app='ai-product-stage6-restart-check';proxy=app+'-gateway';port=host.free_port()
    for name in [app,proxy]:
        if host.docker('inspect',name,check=False).returncode==0:raise SystemExit('Fixture exists')
    try:
        host.network()
        host.command(['docker',*host.restricted('ai-product-p-6e0000000001:1.0.2',name=app,network='ai-products',detach=True,persistent=True)])
        host.health(app);host.gateway(proxy,app,port)
        data=json.loads(host.docker('inspect',app,proxy).stdout)
        assert all(x['HostConfig']['RestartPolicy']['Name']=='unless-stopped' for x in data)
        assert data[1]['HostConfig']['Memory']==64*1024*1024
        assert data[1]['HostConfig']['NanoCpus']==250_000_000
        report={'status':'passed','checks':['persistent service restart policy','persistent trusted gateway restart policy','gateway limited to 64MiB / 0.25 CPU']}
        Path('docs/stage6-runtime-check.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
    finally:
        host.remove_container(proxy);host.remove_container(app)

if __name__=='__main__':main()
