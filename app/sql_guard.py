"""PostgreSQL AST allowlist. Database permissions are an independent boundary."""
from pglast import parse_sql
from pglast.visitors import Visitor

SOURCES={'dim_customer','dim_product','fact_loan','dim_calendar','fact_loan_daily'}
FUNCTIONS={'sum','count','min','max','round','avg','abs','date_trunc','extract','date_part'}
TYPES={'numeric','int2','int4','int8','text','date','bool','float4','float8','varchar','bpchar'}
NODES={'RawStmt','SelectStmt','WithClause','CommonTableExpr','RangeVar','RangeSubselect',
       'JoinExpr','ResTarget','ColumnRef','String','Integer','Float','Boolean','A_Const',
       'A_Expr','BoolExpr','FuncCall','NullTest','BooleanTest','CaseExpr','CaseWhen',
       'CoalesceExpr','TypeCast','TypeName','SortBy','SubLink','Alias','A_Star','GroupingSet'}
OPS={'+','-','*','/','=','<>','!=','>','<','>=','<=','~~','!~~'}

class UnsafeSQL(ValueError): pass


def validate_select(sql):
    if not isinstance(sql,str) or len(sql.encode())>30000:raise UnsafeSQL('SQL exceeds limit')
    try:tree=parse_sql(sql)
    except Exception:raise UnsafeSQL('SQL parse failed') from None
    if len(tree)!=1 or type(tree[0].stmt).__name__!='SelectStmt':raise UnsafeSQL('Exactly one SELECT required')
    ctes=set()
    class CTEs(Visitor):
        def visit_CommonTableExpr(self,anc,node):
            if node.ctename.startswith('pg_') or node.ctename in SOURCES:raise UnsafeSQL('Reserved CTE name')
            ctes.add(node.ctename)
    CTEs()(tree)
    class Guard(Visitor):
        def visit(self,anc,node):
            kind=type(node).__name__
            if kind not in NODES:raise UnsafeSQL('Forbidden AST node: '+kind)
            if kind=='SelectStmt' and (node.intoClause or node.lockingClause):raise UnsafeSQL('INTO/locking forbidden')
            if kind=='WithClause' and node.recursive:raise UnsafeSQL('Recursive SQL forbidden')
            if kind=='RangeVar':
                if node.catalogname:raise UnsafeSQL('Cross-database reference')
                if node.schemaname=='synthetic' and node.relname in SOURCES:return
                if not node.schemaname and node.relname in ctes:return
                raise UnsafeSQL('Only allowlisted synthetic sources and CTEs')
            if kind=='FuncCall':
                parts=[p.sval for p in node.funcname]
                if not (len(parts)==1 or len(parts)==2 and parts[0]=='pg_catalog') or parts[-1].lower() not in FUNCTIONS:
                    raise UnsafeSQL('Function forbidden')
            if kind=='TypeName':
                parts=[p.sval for p in node.names]
                if not (len(parts)==1 or len(parts)==2 and parts[0]=='pg_catalog') or parts[-1] not in TYPES or node.arrayBounds:
                    raise UnsafeSQL('Cast forbidden')
            if kind=='A_Expr':
                parts=[p.sval for p in node.name or ()]
                if parts and (len(parts)!=1 or parts[0] not in OPS):raise UnsafeSQL('Operator forbidden')
    Guard()(tree)
    return sql.strip().rstrip(';')
