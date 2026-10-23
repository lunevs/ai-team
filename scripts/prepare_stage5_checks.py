"""Prepare disposable local recovery fixtures; never replace production volumes.
Requires a verified encrypted backup and an already running local installation.
"""
import argparse,json,os,secrets,subprocess,time
from pathlib import Path
from backup_installation import restore,authenticate_backup,IMAGE
ROOT=Path(__file__).resolve().parents[1];PREFIX='ai-team-dr-5e000002'
GARAGE='dxflrs/garage:v2.4.1@sha256:9c96caa2612d3411acc5b0e6701fb238dbfba33e533a6d7d3d811a4b12d0d020'
MARIA='mariadb:11.4.8@sha256:bc474f00629f0123c10f9e1bca193a45d18af15a274cf0656acda64f1086c3b6'

def command(*args):
    p=subprocess.run(args,capture_output=True,text=True,timeout=180)
    if p.returncode:raise RuntimeError('Fixture command failed: '+args[0])
    return p.stdout

def docker(*args):return command('docker',*args)
def exists(name):return subprocess.run(['docker','container','inspect',name],capture_output=True).returncode==0
def write(path,text,mode=0o600):path.write_text(text);path.chmod(mode)

def main(backup,key):
    os.chdir(ROOT);os.umask(0o077);manifest=authenticate_backup(backup,key)
    existing=set(docker('volume','ls','--format','{{.Name}}').splitlines())
    expected={PREFIX+'_'+v for v in manifest['volumes']}
    if expected<=existing:
        if not(ROOT/'.local'/PREFIX/'.env').exists():raise ValueError('Existing recovery fixtures have no private configuration; inspect/clean them first')
    else:restore(backup,key,PREFIX)
    private=ROOT/'.local/stage5-services';private.mkdir(mode=0o700,parents=True,exist_ok=True)
    tls=private/'tls';tls.mkdir(mode=0o700,exist_ok=True)
    if not(tls/'server.crt').exists():
        command('openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(tls/'server.key'),'-out',str(tls/'server.crt'),'-days','2','-subj','/CN=stage5-postgres','-addext','subjectAltName=DNS:stage5-postgres')
        (tls/'server.crt').chmod(0o644)
    write(tls/'pg_hba.conf','local all all trust\nhostssl all all 0.0.0.0/0 scram-sha-256\nhostssl all all ::0/0 scram-sha-256\n',0o644)
    if not exists('ai-team-stage5-postgres'):
        docker('run','--rm','--network','none','--read-only','--cap-drop','ALL','--cap-add','DAC_OVERRIDE','--cap-add','CHOWN','--cap-add','FOWNER',
            '-v',PREFIX+'_pgdata:/data','-v',str(tls)+':/certs:ro','--entrypoint','/bin/sh',IMAGE,'-ec',
            'cp /certs/server.key /data/stage5-server.key; cp /certs/server.crt /data/stage5-server.crt; cp /certs/pg_hba.conf /data/stage5_hba.conf; chown 70:70 /data/stage5-server.key /data/stage5-server.crt /data/stage5_hba.conf; chmod 600 /data/stage5-server.key')
        docker('run','-d','--name','ai-team-stage5-postgres','--network','ai-team_backend','--network-alias','stage5-postgres','--network-alias','stage5-wrong-host','--user','70:70','--read-only','--tmpfs','/var/run/postgresql:uid=70,gid=70,mode=0770','--tmpfs','/tmp','--cap-drop','ALL','--security-opt','no-new-privileges:true','--memory','256m','-v',PREFIX+'_pgdata:/var/lib/postgresql/data',IMAGE,'postgres','-c','ssl=on','-c','ssl_cert_file=/var/lib/postgresql/data/stage5-server.crt','-c','ssl_key_file=/var/lib/postgresql/data/stage5-server.key','-c','hba_file=/var/lib/postgresql/data/stage5_hba.conf')
    if not exists('ai-team-stage5-mariadb'):
        docker('run','-d','--name','ai-team-stage5-mariadb','--network','none','--user','999:999','--read-only','--tmpfs','/tmp:uid=999,gid=999,mode=0700','--cap-drop','ALL','--security-opt','no-new-privileges:true','--memory','256m','-v',PREFIX+'_bookstack_db:/var/lib/mysql',MARIA,'mariadbd','--socket=/tmp/mysql.sock','--pid-file=/tmp/mysql.pid','--skip-networking')
    garage=private/'garage';garage.mkdir(mode=0o700,exist_ok=True)
    if not exists('ai-team-stage5-garage'):
        write(garage/'garage.toml','metadata_dir="/tmp/meta"\ndata_dir="/tmp/data"\ndb_engine="sqlite"\nreplication_factor=1\nrpc_bind_addr="0.0.0.0:3901"\nrpc_public_addr="127.0.0.1:3901"\nrpc_secret="'+secrets.token_hex(32)+'"\n[s3_api]\ns3_region="garage"\napi_bind_addr="0.0.0.0:3900"\n',0o644)
        values={'GARAGE_DEFAULT_ACCESS_KEY':'GK'+secrets.token_hex(16),'GARAGE_DEFAULT_SECRET_KEY':secrets.token_hex(32),'GARAGE_DEFAULT_BUCKET':'stage5-artifacts','RUST_LOG':'error'}
        write(garage/'garage.env','\n'.join(k+'='+v for k,v in values.items())+'\n')
        docker('run','-d','--name','ai-team-stage5-garage','--network','ai-team_backend','--network-alias','garage','--user','10001:10001','--read-only','--tmpfs','/tmp:uid=10001,gid=10001,mode=0700','--cap-drop','ALL','--security-opt','no-new-privileges:true','--memory','256m','--cpus','0.5','--env-file',str(garage/'garage.env'),'-v',str(garage/'garage.toml')+':/etc/garage.toml:ro',GARAGE,'/garage','server','--single-node','--default-bucket')
        until=time.monotonic()+60
        while time.monotonic()<until:
            if subprocess.run(['docker','exec','ai-team-stage5-garage','/garage','status'],capture_output=True).returncode==0:break
            time.sleep(1)
        else:raise RuntimeError('Garage not ready')
        writer={'S3_ENDPOINT':'http://garage:3900','S3_REGION':'garage','S3_BUCKET':values['GARAGE_DEFAULT_BUCKET'],'S3_ACCESS_KEY_ID':values['GARAGE_DEFAULT_ACCESS_KEY'],'S3_SECRET_ACCESS_KEY':values['GARAGE_DEFAULT_SECRET_KEY'],'S3_ALLOW_LOCAL_HTTP':'true','ARTIFACT_BACKEND':'s3'}
        raw=docker('exec','ai-team-stage5-garage','/garage','key','create','--expires-in','1d','stage5-reader')
        fields=dict(l.split(':',1) for l in raw.splitlines() if ':' in l)
        reader=writer|{'S3_ACCESS_KEY_ID':fields['Key ID'].strip(),'S3_SECRET_ACCESS_KEY':fields['Secret key'].strip()}
        docker('exec','ai-team-stage5-garage','/garage','bucket','allow','--read','--key',reader['S3_ACCESS_KEY_ID'],'stage5-artifacts')
        for name,data in [('writer.env',writer),('reader.env',reader)]:write(garage/name,'\n'.join(k+'='+v for k,v in data.items())+'\n')
    if not(garage/'writer.env').exists() or not(garage/'reader.env').exists():raise ValueError('Private fixture credentials missing')
    print(json.dumps({'prefix':PREFIX,'fixtures':'ready','production_replaced':False,'cloud':False}))
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('backup',type=Path);p.add_argument('--key-file',type=Path,default=ROOT/'.backup-key');a=p.parse_args();main(a.backup.resolve(),a.key_file.resolve())
