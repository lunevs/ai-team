"""Prepare the macOS user operator service. Installation requires explicit consent."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
LABEL='local.ai-team.product-operator'
DOMAIN='gui/'+str(os.getuid())
PLIST=Path.home()/'Library/LaunchAgents'/f'{LABEL}.plist'
STATE=ROOT/'.local/product-operator'

def run(*args):
    return subprocess.run(['launchctl',*args],capture_output=True,text=True)

def configuration():
    return {'Label':LABEL,'ProgramArguments':[shutil.which('python3') or sys.executable,str(ROOT/'scripts/product_operator.py'),'--watch'],
        'WorkingDirectory':str(ROOT),'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':10,
        'EnvironmentVariables':{'PATH':'/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/Applications/Docker.app/Contents/Resources/bin','PYTHONUNBUFFERED':'1'},
        'StandardOutPath':str(STATE/'operator.log'),'StandardErrorPath':str(STATE/'operator-error.log')}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['preview','install','status','uninstall'])
    args=parser.parse_args()
    if args.action=='preview':
        print(json.dumps(configuration(),ensure_ascii=False,indent=2));return
    if sys.platform!='darwin':
        raise SystemExit('On Linux supervise product_operator.py --watch with your service manager.')
    if args.action=='status':
        result=run('print',DOMAIN+'/'+LABEL)
        pid=next((line.split('=',1)[1].strip() for line in result.stdout.splitlines() if line.strip().startswith('pid =')),None)
        print(json.dumps({'installed':PLIST.exists(),'running':bool(pid),'pid':pid}));return
    if args.action=='uninstall':
        run('bootout',DOMAIN+'/'+LABEL)
        if PLIST.exists():
            if plistlib.loads(PLIST.read_bytes()).get('Label')!=LABEL:raise SystemExit('Refuse foreign service file')
            PLIST.unlink()
        print('Operator removed; product containers/data preserved.');return
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    for name in ['operator.log','operator-error.log']:
        path=STATE/name;path.touch(exist_ok=True);path.chmod(0o600)
    PLIST.parent.mkdir(parents=True,exist_ok=True)
    if PLIST.exists() and plistlib.loads(PLIST.read_bytes()).get('Label')!=LABEL:
        raise SystemExit('Refuse foreign service file')
    run('bootout',DOMAIN+'/'+LABEL)
    PLIST.write_bytes(plistlib.dumps(configuration()));PLIST.chmod(0o600)
    if run('bootstrap',DOMAIN,str(PLIST)).returncode:
        raise SystemExit('launchctl bootstrap failed; no credentials displayed')
    run('kickstart',DOMAIN+'/'+LABEL)
    print('Installed per-user product operator. No secrets in LaunchAgent.')

if __name__=='__main__':main()
