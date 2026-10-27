"""Product-owned SQL migrations: atomic, ordered, checksum checked. No team access."""
import hashlib
import os
from pathlib import Path
import psycopg

def migrate():
    with psycopg.connect(os.environ['DATABASE_URL']) as conn:
        conn.execute('SELECT pg_advisory_xact_lock(787401)')
        conn.execute('CREATE TABLE IF NOT EXISTS _product_migrations (name text PRIMARY KEY, sha256 text NOT NULL)')
        applied=dict(conn.execute('SELECT name,sha256 FROM _product_migrations').fetchall())
        paths=sorted(Path('/product/migrations').glob('*.sql'))
        if set(applied)-{p.name for p in paths}:raise ValueError('Applied migration was removed')
        for path in paths:
            sql=path.read_bytes();digest=hashlib.sha256(sql).hexdigest()
            if path.name in applied:
                if applied[path.name]!=digest:raise ValueError('Applied migration changed')
                continue
            from pglast import parse_sql,ast
            statements=parse_sql(sql.decode())
            if any(isinstance(s.stmt,ast.TransactionStmt) for s in statements):raise ValueError('Transaction controls forbidden in a migration')
            conn.execute(sql.decode(),prepare=False)
            conn.execute('INSERT INTO _product_migrations VALUES(%s,%s)',(path.name,digest))

if __name__=='__main__':migrate()
