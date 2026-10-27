"""Real Docker evidence fixtures, synthetic controls; no LLM/Git approvals or CBR."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import uuid
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.product_policy import build_package
from app.product_assurance import make_contract,receipt_valid
from scripts import product_operator as host

KEY='p-f11e00000001'

def fixture(version,total=30,empty_chart=False,empty_file=False,kind='service'):
    value={'total':total,'unit':'RUB','data_origin':'synthetic','charts':[{'series':[{'values':[] if empty_chart else [10,20]}]}]}
    setup="from pathlib import Path\nPath('/data/report.csv').write_text("+repr('total\n' if empty_file else 'total\n30\n')+")\n"
    if kind=='service':
        app=setup+'''import json
from http.server import BaseHTTPRequestHandler,HTTPServer
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  value={'status':'ok'} if self.path=='/health' else RESULT
  data=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 def log_message(self,*args):pass
if __name__=='__main__':HTTPServer(('0.0.0.0',8000),Handler).serve_forever()
'''.replace('RESULT',repr(value))
    else:app=setup+"import json\nPath('/data/result.json').write_text(json.dumps("+repr(value)+"))\n"
    solution=SimpleNamespace(files={'app.py':app,'tests/test_product.py':'import unittest\nclass Smoke(unittest.TestCase):\n def test_python(self):self.assertEqual(1+1,2)\n'},kind=kind,title='Synthetic independent evidence fixture',output_type='dashboard' if kind=='service' else 'file',needs_database=False,architecture='Isolated synthetic runtime for protocol verification.',operating_notes='No external data. Synthetic RUB controls. CSV output.')
    content,_=build_package(solution,KEY,version,uuid.UUID(int=11),int(version.split('.')[-1]))
    check={'id':'sum','criterion':0,'description':'Независимая сумма 10 + 20 RUB','basis':'Утверждённый контроль fixture: 10 + 20 = 30 RUB','source':'result','path':'','pointer':'/total','operator':'number','expected':30,'tolerance':'0'}
    rows=check|{'id':'rows','source':'csv_file','path':'report.csv','pointer':'','operator':'min_count','expected':1}
    plan={'checks':[check,rows],'not_applicable_reason':''}
    contract=make_contract({'package':{'sha256':hashlib.sha256(content).hexdigest()},'specification':{'output_type':solution.output_type}},
        {'pm':{'acceptance_criteria':['Непустой результат и 30 RUB в отчёте']},'revision':1,'plan_digest':'fixture'},plan,plan)
    operation={'operation_id':str(uuid.uuid4()),'operation':'check','assurance_version':1,'verification':contract,'package':{'sha256':hashlib.sha256(content).hexdigest()}}
    return content,operation


def main():
    checks=[]
    for label,kwargs,expected in [('positive',{},True),('empty-chart',{'empty_chart':True},False),('wrong-total',{'total':31},False),('empty-file',{'empty_file':True},False),('job-file',{'kind':'job'},True)]:
        content,operation=fixture('1.0.1',**kwargs)
        report=host.check_package(content,operation)
        assert report['passed']==expected,(label,report)
        assert report.get('tests_passed'),(label,report)
        assert receipt_valid(operation['verification'],report.get('evidence',{}),'check')==expected
        checks.append({'case':label,'passed':True,'self_tests_passed':report['tests_passed'],'business_checks_passed':report['passed'],'evidence':report.get('evidence')})
        print(label+': passed',flush=True)
    job,job_operation=fixture('1.0.1',kind='job');job_qa=host.check_package(job,job_operation)
    job_operation.update(operation='deploy',qa=job_qa)
    for attempt in range(2):
        delivered=host.deploy_package(job,job_operation)
        assert delivered['passed'] and receipt_valid(job_operation['verification'],delivered['evidence'],'deploy'),delivered
    checks.append({'case':'job-delivery-and-identical-retry','passed':True,'evidence':delivered['evidence']})
    host.remove_container('ai-product-'+KEY)
    host.docker('volume','rm','ai-product-data-'+KEY+'-v1-0-1')
    # Deploy good state, then reject changed business result and restore old container.
    content,operation=fixture('1.0.1');qa=host.check_package(content,operation)
    operation.update(operation='deploy',qa=qa)
    deployed=host.deploy_package(content,operation)
    assert deployed['passed'] and receipt_valid(operation['verification'],deployed['evidence'],'deploy'),deployed
    bad,changed=fixture('1.0.2',total=31)
    # Health-only approval is deliberately injected to challenge deployment verification.
    metadata,context=None,None
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        _,context=host.extract(bad,Path(directory))
        tag='ai-product-'+KEY+':1.0.2';host.docker('build','--tag',tag,str(context),timeout=300)
        image=host.docker('image','inspect','--format','{{.Id}}',tag).stdout.decode().strip()
    changed.update(operation='deploy',qa={'image_id':image})
    rejected=host.deploy_package(bad,changed)
    assert not rejected['passed'] and rejected['error_type']=='EvidenceFailed',rejected
    restored=json.loads(host.probe('ai-product-'+KEY,'/result').stdout)
    assert restored['total']==30
    checks.append({'case':'deployment-and-rollback','passed':True,'old_total_restored':restored['total'],'rejected_evidence':rejected['evidence']})
    for name in ['ai-product-'+KEY+'-gateway','ai-product-'+KEY,'ai-product-'+KEY+'-rollback']:host.remove_container(name)
    for version in ['1-0-1','1-0-2']:host.docker('volume','rm','ai-product-data-'+KEY+'-v'+version,check=False)
    output={'status':'passed','scope':'Real Docker, synthetic fixture; no LLM review, Git approval or banking acceptance','checks':checks}
    Path('docs/stage11-runtime-check.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__':main()
