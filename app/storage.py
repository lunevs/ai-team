"""Bounded artifact stores. Fixed names; content-addressed S3 keys; no public URLs."""
from datetime import datetime,timezone
import hashlib,hmac,os,re,tempfile,uuid
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit,quote
import httpx

NAMES={'report.xlsx','query.sql','qa.json','review.json','financial.json','style.md','architecture.md','acceptance.md','dashboard.py'}
MAX_BYTES=8*1024*1024
class StorageError(Exception):pass
class IntegrityError(StorageError):pass
class ArtifactStore(Protocol):
    def put(self,run_id,name,content:bytes)->dict:...
    def get(self,run_id,name,manifest:dict)->bytes:...

def identity(run_id,name):
    if name not in NAMES:raise ValueError('Unknown artifact')
    return uuid.UUID(str(run_id)).hex+'/'+name

def verify(data,manifest):
    if len(data)>MAX_BYTES or len(data)!=manifest['size'] or hashlib.sha256(data).hexdigest()!=manifest['sha256']:
        raise IntegrityError('Artifact integrity check failed')
    return data

def descriptor(data,backend):
    if len(data)>MAX_BYTES:raise StorageError('Artifact exceeds size limit')
    return {'sha256':hashlib.sha256(data).hexdigest(),'size':len(data),'backend':backend}

class LocalStore:
    def __init__(self,root=None):self.root=Path(root or os.getenv('ARTIFACT_ROOT','/data/artifacts'))
    def path(self,run_id,name):return self.root/identity(run_id,name)
    def put(self,run_id,name,content):
        manifest=descriptor(content,'local');path=self.path(run_id,name)
        if path.is_symlink() or path.parent.is_symlink():raise IntegrityError('Artifact path is a link')
        path.parent.mkdir(parents=True,exist_ok=True)
        # Hard-link publishes an already complete file atomically without replacing an existing artifact.
        fd,temporary=tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd,'wb') as f:f.write(content);f.flush();os.fsync(f.fileno())
            try:os.link(temporary,path)
            except FileExistsError:verify(self.get(run_id,name,manifest),manifest)
        finally:os.unlink(temporary)
        return manifest
    def get(self,run_id,name,manifest):
        path=self.path(run_id,name)
        if path.is_symlink() or path.parent.is_symlink():raise IntegrityError('Artifact path is a link')
        try:
            with path.open('rb') as f:data=f.read(MAX_BYTES+1)
        except OSError:raise StorageError('Artifact unavailable') from None
        return verify(data,manifest)

class S3Store:
    """Minimal SigV4 GET/PUT subset; private path-style bucket, no redirects or SDK fallback."""
    def __init__(self):
        self.endpoint=os.getenv('S3_ENDPOINT','').rstrip('/');parts=urlsplit(self.endpoint)
        local=parts.hostname in {'garage','localhost','127.0.0.1'}
        if parts.username or parts.password or parts.query or parts.fragment or parts.path not in {'','/'} or not parts.hostname:
            raise StorageError('Invalid S3 endpoint')
        if parts.scheme!='https' and not(parts.scheme=='http' and local and os.getenv('S3_ALLOW_LOCAL_HTTP')=='true'):
            raise StorageError('S3 requires verified HTTPS')
        self.bucket=os.getenv('S3_BUCKET','');self.region=os.getenv('S3_REGION','')
        self.prefix=os.getenv('S3_PREFIX','artifacts')
        self.access=os.getenv('S3_ACCESS_KEY_ID','');self.secret=os.getenv('S3_SECRET_ACCESS_KEY','')
        if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]',self.bucket) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',self.region):
            raise StorageError('Invalid S3 bucket or region')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',self.prefix) or not self.access or not self.secret:raise StorageError('S3 credentials/configuration missing')
    def request(self,method,key,content=b''):
        now=datetime.now(timezone.utc);stamp=now.strftime('%Y%m%dT%H%M%SZ');day=stamp[:8]
        path='/'+self.bucket+'/'+quote(key,safe='/');scope=day+'/'+self.region+'/s3/aws4_request'
        digest=hashlib.sha256(content).hexdigest()
        headers={'host':urlsplit(self.endpoint).netloc,'x-amz-content-sha256':digest,'x-amz-date':stamp}
        canonical=''.join(k+':'+v+'\n' for k,v in sorted(headers.items()));names=';'.join(sorted(headers))
        request='\n'.join([method,path,'',canonical,names,digest])
        message='AWS4-HMAC-SHA256\n'+stamp+'\n'+scope+'\n'+hashlib.sha256(request.encode()).hexdigest()
        key_material=('AWS4'+self.secret).encode()
        for part in [day,self.region,'s3','aws4_request']:key_material=hmac.new(key_material,part.encode(),hashlib.sha256).digest()
        signature=hmac.new(key_material,message.encode(),hashlib.sha256).hexdigest()
        headers['authorization']='AWS4-HMAC-SHA256 Credential='+self.access+'/'+scope+', SignedHeaders='+names+', Signature='+signature
        try:
            with httpx.Client(timeout=httpx.Timeout(20,connect=5),follow_redirects=False,trust_env=False) as client:
                with client.stream(method,self.endpoint+path,headers=headers,content=content) as response:
                    if response.status_code==404:return None
                    if response.status_code not in [200,201,204]:raise StorageError('S3 operation rejected')
                    result=bytearray()
                    for chunk in response.iter_bytes():
                        result.extend(chunk)
                        if len(result)>MAX_BYTES:raise StorageError('S3 object exceeds size limit')
                    return bytes(result)
        except httpx.HTTPError:raise StorageError('S3 unavailable') from None
    def key(self,run_id,name,manifest):
        if not re.fullmatch('[a-f0-9]{64}',manifest['sha256']):raise IntegrityError('Invalid artifact digest')
        return self.prefix+'/'+identity(run_id,name)+'/'+manifest['sha256']
    def put(self,run_id,name,content):
        manifest=descriptor(content,'s3');key=self.key(run_id,name,manifest)
        existing=self.request('GET',key)
        if existing is not None:verify(existing,manifest)
        else:
            self.request('PUT',key,content)
            saved=self.request('GET',key)
            if saved is None:raise StorageError('S3 write not visible')
            verify(saved,manifest)
        return manifest
    def get(self,run_id,name,manifest):
        result=self.request('GET',self.key(run_id,name,manifest))
        if result is None:raise StorageError('Artifact unavailable')
        return verify(result,manifest)

def store(backend=None):
    backend=backend or os.getenv('ARTIFACT_BACKEND','local')
    if backend=='local':return LocalStore()
    if backend=='s3':return S3Store()
    raise StorageError('Unknown artifact backend')

def read_artifact(run_id,name,manifest):return store(manifest.get('backend','local')).get(run_id,name,manifest)
