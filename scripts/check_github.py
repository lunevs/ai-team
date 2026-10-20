"""Проверяет GitHub через настроенный Git credential helper, не выводя credentials."""
import json
import subprocess
import urllib.request
import urllib.error

try:
    proc = subprocess.run(['git', 'credential', 'fill'], input='protocol=https\nhost=github.com\n\n', text=True, capture_output=True, timeout=15)
    credentials = dict(line.split('=', 1) for line in proc.stdout.splitlines() if '=' in line)
    token = credentials.get('password')
except subprocess.TimeoutExpired:
    token = None
headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
if token:
    headers['Authorization'] = 'Bearer ' + token
for path in ['', '/actions/permissions']:
    request = urllib.request.Request('https://api.github.com/repos/lunevs/ai-team'+path, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.load(response)
        if path:
            print(json.dumps({'actions_permissions': data}))
        else:
            print(json.dumps({k: data.get(k) for k in ['full_name', 'private', 'default_branch', 'permissions', 'license']}))
    except urllib.error.HTTPError as error:
        print(json.dumps({'path': path or '/repo', 'http_status': error.code}))
