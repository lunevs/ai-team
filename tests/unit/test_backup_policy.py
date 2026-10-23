import importlib.util,json
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('backup',Path(__file__).parents[2]/'scripts/backup_installation.py')
backup=importlib.util.module_from_spec(spec);spec.loader.exec_module(backup)

def test_backup_authentication_prevents_tampering_before_restore(tmp_path):
    key=tmp_path/'key';auth=backup.key_material(key,True)
    archive=tmp_path/'pgdata.tar.enc';archive.write_bytes(b'encrypted fixture')
    manifest={'format':1,'image':backup.IMAGE,'volumes':{'pgdata':{'archive':archive.name,'sha256':backup.digest(archive)}}}
    manifest['hmac']=backup.signature(manifest,auth);(tmp_path/'manifest.json').write_text(json.dumps(manifest))
    assert backup.authenticate_backup(tmp_path,key)['format']==1
    archive.write_bytes(b'tampered')
    with pytest.raises(ValueError,match='checksum'):backup.authenticate_backup(tmp_path,key)
    archive.write_bytes(b'encrypted fixture');manifest['image']='attacker-image';(tmp_path/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='authentication'):backup.authenticate_backup(tmp_path,key)
    with pytest.raises(ValueError,match='prefix'):backup.restore(tmp_path,key,'ai-team')

def test_restore_rejects_existing_target_before_any_creation(tmp_path,monkeypatch):
    key=tmp_path/'key';auth=backup.key_material(key,True)
    manifest={'format':1,'image':backup.IMAGE,'volumes':{'pgdata':{'archive':'pgdata.tar.enc','sha256':''}}}
    archive=tmp_path/'pgdata.tar.enc';archive.write_bytes(b'fixture');manifest['volumes']['pgdata']['sha256']=backup.digest(archive)
    manifest['hmac']=backup.signature(manifest,auth);(tmp_path/'manifest.json').write_text(json.dumps(manifest))
    calls=[]
    def docker(*args):
        calls.append(args);assert args==('volume','ls','--format','{{.Name}}')
        return b'ai-team-dr-12345678_pgdata\n'
    monkeypatch.setattr(backup,'docker',docker)
    with pytest.raises(ValueError,match='entirely new'):backup.restore(tmp_path,key,'ai-team-dr-12345678')
    assert len(calls)==1
