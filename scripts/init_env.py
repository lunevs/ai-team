"""Добавляет локальные секреты, не изменяя существующие значения."""
from pathlib import Path
import base64
import secrets

path = Path('.env')
text = path.read_text() if path.exists() else ''
keys = {line.split('=', 1)[0].strip() for line in text.splitlines() if '=' in line}
values = {
    'PRODUCT_OPERATOR_TOKEN': secrets.token_hex(32),
    'POSTGRES_PASSWORD': secrets.token_hex(24),
    'OPENPROJECT_DB_PASSWORD': secrets.token_hex(24),
    'APP_DB_PASSWORD': secrets.token_hex(24),
    'OPENPROJECT_SECRET_KEY_BASE': secrets.token_hex(64),
    'COLLABORATIVE_SERVER_SECRET': secrets.token_hex(32),
    'BOOKSTACK_DB_PASSWORD': secrets.token_hex(24),
    'MARIADB_ROOT_PASSWORD': secrets.token_hex(24),
    'BOOKSTACK_APP_KEY': 'base64:' + base64.b64encode(secrets.token_bytes(32)).decode(),
}
lines=text.splitlines()
for key,value in values.items():
    indices=[i for i,line in enumerate(lines) if line.partition('=')[0].strip()==key]
    if not indices:
        lines.append(f'{key}={value}')
    elif not lines[indices[-1]].partition('=')[2].strip():
        lines[indices[-1]]=f'{key}={value}'
path.write_text('\n'.join(lines)+'\n')
path.chmod(0o600)
print('Локальные настройки дополнены; значения секретов скрыты.')
