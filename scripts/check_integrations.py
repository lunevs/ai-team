"""Реальный последовательный инфраструктурный smoke test, не runtime-адаптер."""
import base64
import json
from pathlib import Path
import urllib.request
import urllib.error

settings = dict(line.split('=', 1) for line in Path('.env').read_text().splitlines() if '=' in line and not line.startswith('#'))

def api(base, auth, path, method='GET', data=None):
    body = None if data is None else json.dumps(data).encode()
    request = urllib.request.Request(base+path, data=body, method=method, headers={'Authorization': auth, 'Content-Type': 'application/json', 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        payload = json.loads(error.read())
        print(json.dumps({'path': path, 'http_status': error.code, 'error': payload.get('message') or payload.get('error')}, ensure_ascii=False))
        raise SystemExit(1)

def op(path, method='GET', data=None):
    auth = 'Basic ' + base64.b64encode(('apikey:'+settings['OPENPROJECT_API_KEY']).encode()).decode()
    return api('http://localhost:8080', auth, path, method, data)

def kb(path, method='GET', data=None):
    auth = 'Token '+settings['BOOKSTACK_TOKEN_ID']+':'+settings['BOOKSTACK_TOKEN_SECRET']
    return api('http://localhost:8082', auth, '/api'+path, method, data)

books = kb('/books')['data']
book = next((b for b in books if b['name'] == 'Bank AI Delivery Team'), None)
if book is None:
    book = kb('/books', 'POST', {'name': 'Bank AI Delivery Team', 'description': 'Спецификации и решения проекта'})
pages = kb('/pages?count=100')['data']
page = next((p for p in pages if p['book_id'] == book['id'] and p['name'] == 'ТЗ и план реализации'), None)
content = {'name': 'ТЗ и план реализации', 'markdown': Path('agent_bank_mvp_spec.md').read_text()}
if page is None:
    page = kb('/pages', 'POST', {'book_id': book['id'], **content})
else:
    page = kb('/pages/'+str(page['id']), 'PUT', content)
page_url = 'http://localhost:8082/books/'+book['slug']+'/page/'+page['slug']
projects = op('/api/v3/projects')['_embedded']['elements']
project = next((p for p in projects if p['identifier'] == 'ai-team'), None)
if project is None:
    project = op('/api/v3/projects', 'POST', {'name': 'Bank AI Delivery Team', 'identifier': 'ai-team', 'public': False})
project_id = project['id']
types = op(f'/api/v3/projects/{project_id}/types')['_embedded']['elements']
task_type = next((t for t in types if t['name'].lower() == 'task'), types[0])
epic_type = next((t for t in types if t['name'].lower() == 'epic'), task_type)
existing = op(f'/api/v3/projects/{project_id}/work_packages?pageSize=100')['_embedded']['elements']

def task(subject, type_, parent=None):
    found = next((t for t in existing if t['subject'] == subject), None)
    if found:
        return found
    links = {'type': {'href': f"/api/v3/types/{type_['id']}"}}
    if parent:
        links['parent'] = {'href': f'/api/v3/work_packages/{parent}'}
    item = op(f'/api/v3/projects/{project_id}/work_packages', 'POST', {'subject': subject, 'description': {'format': 'markdown', 'raw': f'Документация: {page_url}\n\nЭтап 0. Проверка реальных интеграций; отдельные агенты ещё не реализованы.'}, '_links': links})
    existing.append(item)
    return item

epic = task('Этап 0 — инфраструктура и интеграции', epic_type)
infra = task('Проверить Docker, ARM64 и запуск инфраструктуры', task_type, epic['id'])
integrations = task('Проверить OpenProject, BookStack и OpenRouter', task_type, epic['id'])
relations = op(f"/api/v3/work_packages/{infra['id']}/relations")['_embedded']['elements']
def is_dependency(relation):
    source = relation['_links']['from']['href'].rsplit('/', 1)[-1]
    target = relation['_links']['to']['href'].rsplit('/', 1)[-1]
    return (relation['type'] == 'precedes' and source == str(infra['id']) and target == str(integrations['id'])) or (relation['type'] == 'follows' and source == str(integrations['id']) and target == str(infra['id']))

if not any(is_dependency(r) for r in relations):
    op(f"/api/v3/work_packages/{infra['id']}/relations", 'POST', {'type': 'precedes', '_links': {'to': {'href': f"/api/v3/work_packages/{integrations['id']}"}}})
# Check PATCH with optimistic locking and one marker comment.
marker = 'infra-spike-api-check-v1'
activities = op(f"/api/v3/work_packages/{integrations['id']}/activities")['_embedded']['elements']
if not any(marker in a.get('comment', {}).get('raw', '') for a in activities):
    op(f"/api/v3/work_packages/{integrations['id']}/activities", 'POST', {'comment': {'raw': marker+': создание/чтение, комментарии и зависимости проверены.'}})
statuses = op('/api/v3/statuses')['_embedded']['elements']
latest = op(f"/api/v3/work_packages/{integrations['id']}")
op(f"/api/v3/work_packages/{integrations['id']}", 'PATCH', {'lockVersion': latest['lockVersion'], 'description': latest['description']})
progress = next((s for s in statuses if s['name'].lower() == 'in progress'), None)
if progress and latest['_links']['status'].get('title', '').lower() == 'new':
    latest = op(f"/api/v3/work_packages/{integrations['id']}")
    op(f"/api/v3/work_packages/{integrations['id']}", 'PATCH', {'lockVersion': latest['lockVersion'], '_links': {'status': {'href': f"/api/v3/statuses/{progress['id']}"}}})
versions = op(project['_links']['versions']['href'])['_embedded']['elements']
version = next((v for v in versions if v['name'] == f"epic-{epic['id']}-v0.1.0"), None)
if version is None:
    version = op('/api/v3/versions', 'POST', {'name': f"epic-{epic['id']}-v0.1.0", 'status': 'open', '_links': {'definingProject': {'href': f'/api/v3/projects/{project_id}'}}})
latest_epic = op(f"/api/v3/work_packages/{epic['id']}")
op(f"/api/v3/work_packages/{epic['id']}", 'PATCH', {'lockVersion': latest_epic['lockVersion'], '_links': {'version': {'href': f"/api/v3/versions/{version['id']}"}}})
spec = op('/api/v3/spec.json')
wiki_methods = {p: sorted(v.keys()) for p, v in spec.get('paths', {}).items() if 'wiki_pages' in p}
report = {'status': 'passed', 'project_id': project_id, 'epic_id': epic['id'], 'epic_type': latest_epic['_links']['type']['title'], 'release_version_id': version['id'], 'task_ids': [infra['id'], integrations['id']], 'book_id': book['id'], 'page_id': page['id'], 'documentation_url': page_url, 'wiki_api_methods': wiki_methods, 'types': [{'id': t['id'], 'name': t['name']} for t in types], 'idempotency_scope': 'sequential smoke reruns only; concurrent reconciliation belongs to stage 1'}
Path('docs/integration-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
print(json.dumps(report, ensure_ascii=False))
