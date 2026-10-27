"""Bounded declarative checks. No model commands, SQL, host paths or eval."""
import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET
from app.product_assurance import validate_contract, pointer, meaningful, compare, result_ready

# Fixed collector runs in the product's existing restricted container; input is JSON data.
COLLECTOR=r'''
import base64,json,pathlib,stat,sys,urllib.request
request=json.loads(sys.argv[1]);out={};total=0
for source,path in request:
 key=source+':'+path
 try:
  if source=='result':
   if pathlib.Path('/data/result.json').is_file():raw=pathlib.Path('/data/result.json').read_bytes()[:65537]
   else:
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),type('NoRedirect',(urllib.request.HTTPRedirectHandler,),{'redirect_request':lambda *a:None})())
    with opener.open('http://127.0.0.1:8000/result',timeout=5) as response:raw=response.read(65537)
   if len(raw)>65536:raise ValueError()
  else:
   p=pathlib.Path('/data')
   for part in path.split('/'):
    p=p/part
    if p.is_symlink():raise ValueError()
   s=p.stat()
   if not stat.S_ISREG(s.st_mode) or s.st_size>1048576:raise ValueError()
   raw=p.read_bytes()
  total+=len(raw)
  if total>2097152:raise ValueError()
  out[key]={'data':base64.b64encode(raw).decode()}
 except Exception:out[key]={'error':'Результат отсутствует, недоступен или превышает допустимый размер'}
print(json.dumps(out))
'''


def decode(raw,source):
    if source in {'result','json_file'}:return json.loads(raw,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Nonfinite JSON')))
    if source=='csv_file':return list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
    # Numeric/string cells keyed by sheet file and cell reference: /sheet1/A1.
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if sum(i.file_size for i in archive.infolist())>8_000_000 or len(archive.infolist())>200:raise ValueError('Oversized XLSX')
        if source=='pptx_file':
            ns={'a':'http://schemas.openxmlformats.org/drawingml/2006/main'}
            names=sorted(n for n in archive.namelist() if __import__('re').fullmatch(r'ppt/slides/slide[0-9]+\.xml',n))
            return {'slides':[' '.join(t.text or '' for t in ET.fromstring(archive.read(n)).findall('.//a:t',ns)) for n in names]}
        ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'};strings=[]
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings=[''.join(n.itertext()) for n in ET.fromstring(archive.read('xl/sharedStrings.xml')).findall('s:si',ns)]
        sheets={}
        for name in archive.namelist():
            if not __import__('re').fullmatch(r'xl/worksheets/sheet[0-9]+\.xml',name):continue
            cells={}
            for c in ET.fromstring(archive.read(name)).findall('.//s:c',ns):
                v=c.find('s:v',ns)
                # Formula cached values are not proof of recalculation.
                if c.find('s:f',ns) is not None:raise ValueError('Formula results require independent recalculation')
                if c.get('t')=='inlineStr':value=''.join(c.find('s:is',ns).itertext())
                elif v is None:continue
                else:value=strings[int(v.text)] if c.get('t')=='s' else v.text
                cells[c.attrib['r']]=value
            sheets[Path(name).stem]=cells
        return sheets


def evaluate(contract,phase,collected):
    validate_contract(contract);checks=[];outputs=[]
    def data(source,path):
        item=collected[source+':'+path]
        raw=base64.b64decode(item['data'],validate=True)
        if len(raw)>(65536 if source=='result' else 1048576):raise ValueError('Oversized result')
        return decode(raw,source)
    try:
        result=data('result','')
        passed=result_ready(result,contract['output_type'])
        actual=result
    except Exception:passed=False;actual=None
    checks.append({'role':'platform','id':'result','criterion':None,'description':'Доступен содержательный результат; графики содержат числовые данные','passed':passed,'actual':actual,'expected':'Непустой результат','reason':'' if passed else 'Результат отсутствует или графики пусты'})
    for role in ('qa','financial_analyst'):
        for c in contract[role]['checks']:
            try:
                actual=pointer(data(c['source'],c['path']),c['pointer']);passed=compare(actual,c)
                reason='' if passed else 'Фактическое значение не соответствует независимой проверке'
            except Exception:actual=None;passed=False;reason='Не удалось получить или прочитать результат проверки'
            checks.append({'role':role,'id':c['id'],'criterion':c['criterion'],'description':c['description'],'passed':passed,
                           'actual':actual,'expected':c['expected'],'reason':reason})
    paths={c['path'] for role in ('qa','financial_analyst') for c in contract[role]['checks'] if c['source']!='result'}
    for path in sorted(paths):
        candidates=[v for k,v in collected.items() if k.endswith(':'+path)]
        if candidates and candidates[0].get('data'):
            raw=base64.b64decode(candidates[0]['data'],validate=True)
            if len(raw)>1048576:raise ValueError('Oversized output')
            outputs.append({'path':path,'sha256':hashlib.sha256(raw).hexdigest(),'data':candidates[0]['data']})
    passed=all(c['passed'] for c in checks)
    if contract['output_type'] in {'file','presentation'} and not outputs:passed=False
    return {'contract_digest':contract['digest'],'package_sha256':contract['package_sha256'],'phase':phase,'checks':checks,'outputs':outputs,'passed':passed}


def verify(host,name,operation,report):
    contract=operation.get('verification')
    if not contract:
        if operation.get('assurance_version')==1:raise host.OperationFailed('PolicyRejected')
        return
    validate_contract(contract)
    if contract['package_sha256']!=operation['package']['sha256']:raise host.OperationFailed('PolicyRejected')
    requests=sorted({('result','')}|{(c['source'],c['path']) for role in ('qa','financial_analyst') for c in contract[role]['checks']})
    response=host.docker('exec',name,'python','-c',COLLECTOR,json.dumps(requests),check=False,timeout=20)
    try:receipt=evaluate(contract,operation['operation'],json.loads(response.stdout))
    except Exception:raise host.OperationFailed('PolicyRejected')
    report['evidence']=receipt
    if not receipt['passed']:raise host.OperationFailed('EvidenceFailed')
