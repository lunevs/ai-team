"""Минимальный реальный smoke test. Не выводит ключ или полный ответ модели."""
import json
import os
from pathlib import Path
import urllib.request
import urllib.error

for line in Path('.env').read_text().splitlines():
    if '=' in line and not line.lstrip().startswith('#'):
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip().strip('\"\''))
key = os.environ.get('OPENROUTER_API_KEY')
model = os.environ.get('OPENROUTER_MODEL')
if not key or not model:
    raise SystemExit('SKIP: OPENROUTER_API_KEY или OPENROUTER_MODEL отсутствует')
payload = {'model': model, 'messages': [{'role': 'user', 'content': 'Return only the JSON object {"ok": true}.'}], 'max_tokens': 64, 'temperature': 0, 'response_format': {'type': 'json_object'}}
request = urllib.request.Request('https://openrouter.ai/api/v1/chat/completions', data=json.dumps(payload).encode(), headers={'Authorization': 'Bearer '+key, 'Content-Type': 'application/json'})
try:
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.load(response)
    content = result['choices'][0]['message']['content']
    valid = json.loads(content) == {'ok': True}
    print(json.dumps({'status': 'passed' if valid else 'failed', 'model': result.get('model'), 'structured_json_valid': valid, 'usage': result.get('usage', {})}))
    if not valid:
        raise SystemExit(1)
except urllib.error.URLError as error:
    if not isinstance(error, urllib.error.HTTPError):
        print(json.dumps({'status': 'failed', 'error_type': type(error.reason).__name__}))
        raise SystemExit(1)
    print(json.dumps({'status': 'failed', 'http_status': error.code}))
    raise SystemExit(1)

