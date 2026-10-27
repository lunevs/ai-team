"""Pure standard-library workspace policy, shared by API, broker, operator and CI."""
import hashlib
import io
import json
import zipfile
from app.product_policy import safe_path,validate_sources,MAX_PACKAGE

def file_hash(text):return hashlib.sha256(text.encode()).hexdigest()
def source_digest(files):return file_hash(json.dumps(files,sort_keys=True,ensure_ascii=False))
def inventory(files):return [{'path':p,'sha256':file_hash(t),'size':len(t)} for p,t in sorted(files.items())]


def validate_workspace(files):
    if len(files)>256:raise ValueError('Workspace file limit')
    validate_sources(files)


def writable(role,path):
    safe_path(path)
    if path.startswith('docs/tasks/'):return False
    if role=='application_developer':return True
    if role=='data_developer':return path.startswith(('data/','dags/','migrations/','tests/','docs/'))
    if role=='qa':return path.startswith(('tests/','docs/'))
    return path.startswith('docs/')


def apply_action(files,role,action):
    files=dict(files)
    if action.operation=='list':return files,{'files':inventory(files)},None
    if action.operation=='search':
        if not action.content:raise ValueError('Search text required')
        hits=[]
        for path,text in sorted(files.items()):
            for number,line in enumerate(text.splitlines(),1):
                if action.content in line:hits.append({'path':path,'line':number,'text':line[:200]})
                if len(hits)>=30:return files,{'matches':hits,'truncated':True},None
        return files,{'matches':hits},None
    if action.operation=='source':return files,{'source_request':action.path},'research:'+action.path
    if action.operation in ['check','finish']:return files,{'summary':action.summary},'continue' if action.operation=='check' else 'finish'
    path=safe_path(action.path)
    if action.operation=='read':
        if path not in files:raise ValueError('File not found')
        return files,{'path':path,'sha256':file_hash(files[path]),'content':files[path][action.offset:action.offset+action.limit],
                      'next_offset':min(len(files[path]),action.offset+action.limit),'size':len(files[path])},None
    if not writable(role,path):raise ValueError('Role cannot change this path')
    expected=file_hash(files[path]) if path in files else 'new'
    if action.expected_sha256!=expected:raise ValueError('Read current file hash before changing it')
    if action.operation=='write':files[path]=action.content
    elif action.operation=='edit':
        if path not in files or not action.old_text or files[path].count(action.old_text)!=1:raise ValueError('Edit must match exactly once')
        files[path]=files[path].replace(action.old_text,action.new_text,1)
    elif action.operation=='delete':
        if path not in files:raise ValueError('File not found')
        del files[path]
    validate_workspace(files)
    return files,{'path':path,'sha256':file_hash(files[path]) if path in files else None,'source_digest':source_digest(files)},None


def snapshot(files,metadata):
    validate_workspace(files)
    validate_profile_metadata(metadata)
    validate_sources(files,metadata.get('runtime_profile','python-locked-v1'))
    manifest=metadata|{'format':1,'source_digest':source_digest(files),'files':{p:{'sha256':file_hash(t),'size':len(t.encode())} for p,t in sorted(files.items())}}
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        payload={'workspace.json':json.dumps(manifest,sort_keys=True,ensure_ascii=False).encode()}
        payload.update({'source/'+p:t.encode() for p,t in files.items()})
        for path,data in sorted(payload.items()):
            info=zipfile.ZipInfo(path,(2020,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;z.writestr(info,data)
    content=output.getvalue()
    if len(content)>MAX_PACKAGE:raise ValueError('Workspace snapshot too large')
    return content,manifest


def inspect_snapshot(content):
    if len(content)>MAX_PACKAGE:raise ValueError('Snapshot too large')
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        names=z.namelist()
        if len(names)!=len(set(names)) or 'workspace.json' not in names:raise ValueError('Invalid snapshot')
        if sum(i.file_size for i in z.infolist())>MAX_PACKAGE:raise ValueError('Expanded snapshot too large')
        manifest=json.loads(z.read('workspace.json'));files={}
        if manifest.get('format')!=1:raise ValueError('Unsupported workspace format')
        if set(names)!={'workspace.json'}|{'source/'+p for p in manifest['files']}:raise ValueError('Snapshot contains extra files')
        for path,expected in manifest['files'].items():
            safe_path(path);data=z.read('source/'+path)
            if len(data)!=expected['size'] or hashlib.sha256(data).hexdigest()!=expected['sha256']:raise ValueError('Snapshot content changed')
            files[path]=data.decode()
        validate_workspace(files)
        validate_profile_metadata(manifest)
        validate_sources(files,manifest.get('runtime_profile','python-locked-v1'))
        if source_digest(files)!=manifest['source_digest']:raise ValueError('Workspace digest changed')
        return manifest,files


def repeated_action(files,action,observations):
    signature=file_hash(json.dumps({'source_digest':source_digest(files),'action':action.model_dump(mode='json')|{'summary':''}},sort_keys=True,ensure_ascii=False))
    return signature,sum(row.get('_action_signature')==signature for row in observations)>=2


def validate_profile_metadata(metadata):
    from app.source_policy import sources_policy
    profile=metadata.get('runtime_profile','python-locked-v1')
    if profile not in {'python-locked-v1','airflow-postgres-v1'}:raise ValueError('Unsupported workspace profile')
    sources=sources_policy(metadata.get('sources',[]))
    if sources and profile!='airflow-postgres-v1':raise ValueError('Sources require isolated profile')
    request=metadata.get('research_source')
    if request and request not in {s['id'] for s in sources}:raise ValueError('Source not approved in the plan')
