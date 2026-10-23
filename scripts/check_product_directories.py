"""CI: validate repository product packages, then run isolated build/tests; no team secrets."""
import io
import json
from pathlib import Path
import re
import sys
import uuid
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.product_policy import RESERVED,safe_path,MAX_PACKAGE
from scripts.product_operator import check_package


def main():
    for manifest in sorted(Path('products').glob('*/product.json')):
        root=manifest.parent
        if root.is_symlink() or manifest.is_symlink():raise ValueError('Linked product directory')
        metadata=json.loads(manifest.read_text())
        names=set(metadata['files'])|{'product.json'}
        if len(names)>50:raise ValueError('Too many product files')
        output=io.BytesIO();total=0
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(names):
                if name not in RESERVED:safe_path(name)
                path=root/name
                if any(p.is_symlink() for p in [path,*path.parents]):raise ValueError('Linked product file')
                total+=path.stat().st_size
                if total>MAX_PACKAGE:raise ValueError('Oversized package')
                archive.writestr('products/'+root.name+'/'+name,path.read_bytes())
        result=check_package(output.getvalue(),{'operation_id':str(uuid.uuid4())})
        if not result['passed']:raise RuntimeError('Product checks failed: '+result.get('error_type','unknown'))
        print(json.dumps({'product_key':root.name,'checks':'passed'}))

if __name__=='__main__':main()
