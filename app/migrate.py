"""Запускать только с учётной записью миграций, до api/worker."""
from pathlib import Path
import hashlib
from langgraph.checkpoint.postgres import PostgresSaver
from app.db import connection
from app.config import settings

with connection() as conn:
    conn.execute('SELECT pg_advisory_xact_lock(4182001)')
    conn.execute('CREATE TABLE IF NOT EXISTS schema_migrations(name text PRIMARY KEY, checksum text NOT NULL)')
    for path in sorted(Path(__file__).with_name('migrations').glob('*.sql')):
        sql=path.read_text()
        checksum=hashlib.sha256(sql.encode()).hexdigest()
        row=conn.execute('SELECT checksum FROM schema_migrations WHERE name=%s',(path.name,)).fetchone()
        if row and row['checksum']!=checksum:
            raise RuntimeError('Applied migration checksum changed: '+path.name)
        if not row:
            conn.execute(sql)
            conn.execute('INSERT INTO schema_migrations VALUES(%s,%s)',(path.name,checksum))
with PostgresSaver.from_conn_string(settings.database_url) as saver:
    saver.setup()
with connection() as conn:
    conn.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO ai_team_runtime')
    conn.execute('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO ai_team_runtime')
print('Application migrations and LangGraph checkpoints ready.')
