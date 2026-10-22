"""No LLM/Git credentials, no application DB credentials, no arbitrary commands."""
import hashlib
import os
import uuid
from decimal import Decimal
from datetime import date,datetime
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from fastapi import FastAPI,Header,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from app.sql_guard import validate_select,UnsafeSQL

app=FastAPI(title='SQL sandbox',version='0.4.0')
class Query(BaseModel):
    model_config=ConfigDict(extra='forbid')
    run_id:uuid.UUID
    attempt:int=Field(ge=1,le=3)
    select_sql:str=Field(max_length=30000)


def connection():return psycopg.connect(os.environ['SANDBOX_DATABASE_URL'],row_factory=dict_row,connect_timeout=5)

def auth(authorization):
    import hmac
    expected=os.getenv('SANDBOX_API_TOKEN','')
    if not expected or not hmac.compare_digest(authorization or '','Bearer '+expected):raise HTTPException(401,'Unauthorized')

@app.get('/health')
def health():return {'status':'ok'}

@app.post('/execute')
def execute(data:Query,authorization:str=Header(default='')):
    auth(authorization)
    try:query=validate_select(data.select_sql)
    except UnsafeSQL as exc:raise HTTPException(422,str(exc)) from None
    name='r_'+data.run_id.hex+'_a'+str(data.attempt)
    digest=hashlib.sha256(query.encode()).hexdigest()
    try:
        with connection() as conn:
            conn.execute("SET LOCAL statement_timeout='5s'")
            conn.execute("SET LOCAL lock_timeout='1s'")
            conn.execute("SET LOCAL search_path=pg_catalog,synthetic")
            conn.execute("SET LOCAL work_mem='16MB'")
            conn.execute('SELECT pg_advisory_xact_lock(%s)',(int(data.run_id.int%((1<<63)-1)),))
            # Replacing only this deterministic attempt table is retry-safe.
            conn.execute(sql.SQL('DROP TABLE IF EXISTS sandbox.{}').format(sql.Identifier(name)))
            conn.execute(sql.SQL('CREATE TABLE sandbox.{} AS SELECT * FROM ({}) AS result LIMIT 1001').format(sql.Identifier(name),sql.SQL(query)))
            rows=conn.execute(sql.SQL('SELECT * FROM sandbox.{} LIMIT 1001').format(sql.Identifier(name))).fetchall()
            if len(rows)>1000:raise UnsafeSQL('Result row limit exceeded')
            # All generated SQL above is local; model receives neither credentials nor table identifiers.
            return {'table':'sandbox.'+name,'sql_digest':digest,'rows':[
                {k:str(v) if isinstance(v,(Decimal,date,datetime)) else v for k,v in row.items()} for row in rows]}
    except UnsafeSQL as exc:raise HTTPException(422,str(exc)) from None
    except psycopg.errors.QueryCanceled:raise HTTPException(422,'SQL timeout') from None
    except psycopg.Error as exc:
        # SQLSTATE is enough for a bounded repair without revealing DB internals.
        raise HTTPException(422,'SQL execution failed; SQLSTATE '+str(exc.sqlstate)) from None
