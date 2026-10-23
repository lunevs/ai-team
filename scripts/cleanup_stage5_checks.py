"""Remove only named test containers and labelled recovery volumes; never source volumes/backups/key."""
import argparse,re,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CONTAINERS=['ai-team-stage5-worker','ai-team-stage5-api','ai-team-stage5-sandbox','ai-team-stage5-git-mock','ai-team-stage5-mariadb','ai-team-stage5-postgres','ai-team-stage5-garage']
def main(prefix,remove_volumes):
    if not re.fullmatch('ai-team-dr-[a-f0-9]{8}',prefix):raise ValueError('Invalid disposable prefix')
    for name in CONTAINERS:subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=40)
    subprocess.run(['docker','network','rm','ai-team-stage5-execution'],capture_output=True,timeout=20)
    if remove_volumes:
        result=subprocess.run(['docker','volume','ls','--filter','label=ai-team.restore='+prefix,'--format','{{.Name}}'],capture_output=True,text=True,check=True)
        for name in result.stdout.splitlines():
            if not name.startswith(prefix+'_'):raise ValueError('Unexpected volume label')
            subprocess.run(['docker','volume','rm',name],capture_output=True,check=True,timeout=30)
        shutil.rmtree(ROOT/'.local'/prefix,ignore_errors=True)
    for directory in ['stage5-check','stage5-services']:shutil.rmtree(ROOT/'.local'/directory,ignore_errors=True)
    print('Disposable fixtures removed; production, encrypted backup and backup key preserved')
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--prefix',default='ai-team-dr-5e000002');p.add_argument('--remove-volumes',action='store_true');a=p.parse_args();main(a.prefix,a.remove_volumes)
