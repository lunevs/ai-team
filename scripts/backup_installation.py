"""Operator-only encrypted cold snapshots; restore ONLY to new isolated volumes.
No Docker access is added to runtime containers. Never prints secrets/container env.
"""
import argparse,hashlib,hmac,json,os,re,secrets,subprocess,tempfile,time
from pathlib import Path

IMAGE='postgres:17.6-alpine@sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94'
ROOT=Path(__file__).resolve().parents[1]

def run(*args,**kwargs):
    result=subprocess.run(args,capture_output=True,check=False,**kwargs)
    if result.returncode:raise RuntimeError('Operator command failed: '+args[0])
    return result.stdout

def docker(*args):return run('docker',*args)
def compose(*args):return docker('compose','--profile','*',*args)
def digest(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def key_material(path,create=False):
    if not path.exists() and create:
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as f:f.write(secrets.token_hex(32)+'\n')
    if path.is_symlink() or path.stat().st_mode & 0o077:raise ValueError('Backup key must be a private regular file (0600)')
    value=path.read_text().strip()
    if not re.fullmatch('[a-f0-9]{64}',value):raise ValueError('Invalid backup key')
    return hmac.new(bytes.fromhex(value),b'ai-team-backup-auth-v1',hashlib.sha256).digest()
def signature(manifest,key):return hmac.new(key,json.dumps(manifest,sort_keys=True,separators=(',',':')).encode(),hashlib.sha256).hexdigest()
def helper(volume,read_only=True):
    caps=['--cap-add','DAC_OVERRIDE']
    if not read_only:caps+=['--cap-add','CHOWN','--cap-add','FOWNER']
    return ['docker','run','--rm',*(['-i'] if not read_only else []),'--network','none','--read-only','--cap-drop','ALL',*caps,
        '--security-opt','no-new-privileges:true','--user','0:0','-v',volume+':/volume'+(':ro' if read_only else ''),
        '--entrypoint','/bin/tar',IMAGE]

def encrypt_configuration(destination,key_path,manifest):
    encrypted=destination/'deployment.env.enc'
    run('openssl','enc','-aes-256-cbc','-salt','-pbkdf2','-iter','200000','-pass','file:'+str(key_path),'-in',str(ROOT/'.env'),'-out',str(encrypted))
    encrypted.chmod(0o600)
    manifest['configuration']={'archive':encrypted.name,'sha256':digest(encrypted)}

def snapshot(destination,key_path):
    os.chdir(ROOT);os.umask(0o077)
    destination.mkdir(mode=0o700,parents=True,exist_ok=False)
    auth=key_material(key_path,True)
    config=json.loads(compose('config','--format','json'))
    # Remote S3 objects need a separate provider-side backup; never claim volumes cover them.
    if config['services']['worker']['environment'].get('ARTIFACT_BACKEND','local')!='local':
        raise ValueError('Cold volume backup covers local artifacts only; provide an S3 backup plan first')
    existing=set(docker('volume','ls','--format','{{.Name}}').decode().splitlines())
    volumes={k:v['name'] for k,v in config['volumes'].items() if v['name'] in existing}
    containers=compose('ps','--status','running','-q').decode().split()
    if not containers:raise ValueError('No running installation found')
    manifest={'format':1,'created_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'source_commit':run('git','rev-parse','HEAD').decode().strip(),
        'method':'cold-volume snapshot; all source Compose containers stopped','image':IMAGE,'external_s3_included':False,'volumes':{}}
    try:
        docker('stop','--time','30',*containers)
        with tempfile.TemporaryDirectory(prefix='clear-',dir=destination) as temporary:
            for logical,name in sorted(volumes.items()):
                raw=Path(temporary)/'volume.tar';encrypted=destination/(logical+'.tar.enc')
                with raw.open('wb') as output:
                    proc=subprocess.run(helper(name)+['-C','/volume','-cf','-','.'],stdout=output,stderr=subprocess.PIPE)
                if proc.returncode:raise RuntimeError('Volume snapshot failed')
                run('openssl','enc','-aes-256-cbc','-salt','-pbkdf2','-iter','200000','-pass','file:'+str(key_path),'-in',str(raw),'-out',str(encrypted))
                encrypted.chmod(0o600)
                manifest['volumes'][logical]={'archive':encrypted.name,'sha256':digest(encrypted),'bytes':encrypted.stat().st_size}
                raw.unlink()
        encrypt_configuration(destination,key_path,manifest)
        manifest['hmac']=signature(manifest,auth)
        (destination/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    finally:
        docker('start',*containers)
    return {'backup':str(destination),'volume_count':len(volumes),'encrypted':True,'source_restarted':True}

def authenticate_backup(source,key_path):
    manifest=json.loads((source/'manifest.json').read_text());tag=manifest.pop('hmac')
    if not hmac.compare_digest(tag,signature(manifest,key_material(key_path))):raise ValueError('Backup authentication failed')
    if manifest['format']!=1 or manifest['image']!=IMAGE:raise ValueError('Unsupported backup version')
    for logical,item in manifest['volumes'].items():
        if not re.fullmatch('[a-z_]+',logical) or item['archive']!=logical+'.tar.enc':raise ValueError('Invalid archive name')
        if digest(source/item['archive'])!=item['sha256']:raise ValueError('Backup ciphertext checksum failed')
    config=manifest.get('configuration')
    if config:
        if config['archive']!='deployment.env.enc' or digest(source/config['archive'])!=config['sha256']:
            raise ValueError('Backup configuration checksum failed')
    return manifest

def restore(source,key_path,prefix):
    os.umask(0o077)
    if not re.fullmatch(r'ai-team-dr-[a-f0-9]{8}',prefix):raise ValueError('Restore prefix must be ai-team-dr-<8 hex characters>')
    manifest=authenticate_backup(source,key_path)
    targets={logical:prefix+'_'+logical for logical in manifest['volumes']}
    existing=set(docker('volume','ls','--format','{{.Name}}').decode().splitlines())
    if existing & set(targets.values()):raise ValueError('Restore requires entirely new volumes')
    created=[]
    with tempfile.TemporaryDirectory(prefix='ai-team-restore-') as temporary:
        for logical,name in targets.items():
            raw=Path(temporary)/'volume.tar'
            run('openssl','enc','-d','-aes-256-cbc','-pbkdf2','-iter','200000','-pass','file:'+str(key_path),'-in',str(source/manifest['volumes'][logical]['archive']),'-out',str(raw))
            docker('volume','create','--label','ai-team.restore='+prefix,name);created.append(name)
            with raw.open('rb') as input_:
                proc=subprocess.run(helper(name,False)+['-C','/volume','-xpf','-'],stdin=input_,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            if proc.returncode:raise RuntimeError('Restore failed; isolated volumes retained for inspection')
            raw.unlink()
    configuration=None
    if manifest.get('configuration'):
        config_dir=ROOT/'.local'/prefix;config_dir.mkdir(mode=0o700,parents=True,exist_ok=False)
        configuration=config_dir/'.env'
        run('openssl','enc','-d','-aes-256-cbc','-pbkdf2','-iter','200000','-pass','file:'+str(key_path),'-in',str(source/'deployment.env.enc'),'-out',str(configuration))
        configuration.chmod(0o600)
    return {'prefix':prefix,'volumes':targets,'configuration':str(configuration) if configuration else None,'production_replaced':False,'source_commit':manifest['source_commit']}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=['snapshot','verify','restore']);parser.add_argument('directory',type=Path)
    parser.add_argument('--key-file',type=Path,default=ROOT/'.backup-key');parser.add_argument('--prefix')
    args=parser.parse_args();key=args.key_file.resolve();directory=args.directory.resolve()
    if args.command=='snapshot':result=snapshot(directory,key)
    elif args.command=='verify':result={'authenticated':bool(authenticate_backup(directory,key))}
    else:result=restore(directory,key,args.prefix or '')
    print(json.dumps(result))
if __name__=='__main__':main()
