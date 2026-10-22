"""Local profile settings only. Secrets never leave .env / container environments."""
from pathlib import Path
import secrets

path=Path('.env')
if not path.exists():raise SystemExit('Run scripts/init_env.py first')
content=path.read_text()
values=dict(line.split('=',1) for line in content.splitlines() if '=' in line and not line.startswith('#'))
new={key:secrets.token_hex(32) for key in ['GRAFANA_ADMIN_PASSWORD','TELEMETRY_METRICS_TOKEN'] if not values.get(key)}
if not values.get('GRAFANA_PUBLIC_URL'):new['GRAFANA_PUBLIC_URL']='http://127.0.0.1:3001'
if not values.get('OTEL_EXPORTER_OTLP_ENDPOINT'):
    content='\n'.join(line for line in content.splitlines() if not line.startswith('OTEL_EXPORTER_OTLP_ENDPOINT='))+'\n'
    new['OTEL_EXPORTER_OTLP_ENDPOINT']='http://otel-collector:4318'
content+='\n'+''.join(key+'='+value+'\n' for key,value in new.items())
path.write_text(content);path.chmod(0o600)
print('Observability configured; Grafana username admin; password and metrics token only in local .env.')
