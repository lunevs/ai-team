"""External DB connection policy without printing connection strings."""
from psycopg.conninfo import conninfo_to_dict

def database_parameters(url):
    try:params=conninfo_to_dict(url)
    except Exception:raise ValueError('Invalid database configuration') from None
    host=params.get('host','');local=host in {'postgres','localhost','127.0.0.1','::1'}
    if not host or ',' in host or params.get('hostaddr'):
        raise ValueError('An explicit single PostgreSQL hostname is required')
    if not local and (params.get('sslmode')!='verify-full' or not params.get('sslrootcert')):
        raise ValueError('External PostgreSQL requires verify-full and an explicit CA file')
    if not params.get('dbname') or not params.get('user'):raise ValueError('Database name and role required')
    return params
