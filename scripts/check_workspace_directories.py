"""CI: every task source snapshot is checked in an isolated trusted Python container."""
import io
import json
from pathlib import Path
import sys
import uuid
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.workspace_policy import inspect_snapshot
from scripts.workspace_operator import check_snapshot


def main():
    for manifest in sorted(Path('workspaces').glob('*/workspace.json')):
        root=manifest.parent;metadata=json.loads(manifest.read_text());output=io.BytesIO()
        names={'workspace.json'}|{'source/'+p for p in metadata['files']}
        actual={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()}
        if actual!=names:raise ValueError('Workspace contains unlisted files')
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
            for name in sorted(names):
                path=root/name
                if any(p.is_symlink() for p in [path,*path.parents]):raise ValueError('Linked workspace source')
                z.writestr(name,path.read_bytes())
        content=output.getvalue();inspect_snapshot(content)
        import hashlib
        operation={'operation_id':str(uuid.uuid4()),'task_id':metadata['task_id'],'snapshot_sha256':hashlib.sha256(content).hexdigest()}
        import tempfile
        with tempfile.TemporaryDirectory() as directory:result=check_snapshot(content,operation,Path(directory))
        if not result['passed']:raise RuntimeError('Workspace checks failed')
        print(json.dumps({'workspace':root.name,'task':metadata['task_id'],'tests_run':result['tests_run']}))

if __name__=='__main__':main()
