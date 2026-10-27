import base64
import copy
import json
import pytest
from app.product_assurance import make_contract,receipt_valid,ready,compare,validate_plan
from scripts.product_verifier import evaluate


def contract(output='scalar'):
    check={'id':'total','criterion':0,'description':'Сверить независимый контроль суммы','basis':'Заранее рассчитано: 10 + 20 = 30 рублей','source':'result','path':'','pointer':'/total','operator':'number','expected':'30','tolerance':'0.01'}
    plan={'checks':[check],'not_applicable_reason':''}
    return make_contract({'package':{'sha256':'a'*64},'specification':{'output_type':output}},
                         {'pm':{'acceptance_criteria':['Сумма равна 30 RUB']},'revision':1,'plan_digest':'plan'},plan,copy.deepcopy(plan))


def collected(value):return {'result:':{'data':base64.b64encode(json.dumps(value).encode()).decode()}}


def test_independent_numeric_control_rejects_empty_wrong_and_nonfinite():
    c=contract()
    for value in ({},{'total':None},{'total':31},{'total':True},{'total':float('nan')}):
        assert not evaluate(c,'check',collected(value))['passed']
    assert evaluate(c,'check',collected({'total':30}))['passed']


def test_empty_chart_rejected_even_when_numeric_total_correct():
    c=contract('dashboard')
    for charts in ([],[{'series':[]}],[{'series':[{'values':[]}]}],[{'series':[{'values':['caption']}]}]):
        assert not evaluate(c,'check',collected({'total':30,'charts':charts}))['passed']
    assert evaluate(c,'check',collected({'total':30,'charts':[{'series':[{'values':[0,30]}]}]}))['passed']


def test_receipt_is_bound_to_phase_package_contract_and_actual_values():
    c=contract();r=evaluate(c,'check',collected({'total':30}))
    assert receipt_valid(c,r,'check')
    for key,value in [('phase','deploy'),('package_sha256','b'*64),('contract_digest','b'*64)]:
        altered=copy.deepcopy(r);altered[key]=value;assert not receipt_valid(c,altered,'check')
    altered=copy.deepcopy(r);altered['checks'][1]['actual']=40
    assert not receipt_valid(c,altered,'check')
    altered=copy.deepcopy(r);altered['checks'].pop();assert not receipt_valid(c,altered,'check')


def test_acceptance_requires_all_three_reviews_and_both_receipts():
    c=contract();record={'assurance_version':1,'verification':c,'package':{'sha256':'a'*64},
        'qa':{'evidence':evaluate(c,'check',collected({'total':30}))},'deployment':{'evidence':evaluate(c,'deploy',collected({'total':30}))},
        'review':{'contract_digest':c['digest'],**{role:{'approved':True} for role in ('qa','architecture','financial')}}}
    assert ready(record)
    altered=copy.deepcopy(record);altered['review']['financial']['approved']=False;assert not ready(altered)
    altered=copy.deepcopy(record);altered['package']['sha256']='b'*64;assert not ready(altered)
    assert not ready(record,{'revision':2,'plan_digest':'plan','pm':{'acceptance_criteria':c['criteria']}})


def test_path_traversal_zero_counts_and_missing_criteria_fail_closed():
    c=contract();p=copy.deepcopy(c['qa']);check=p['checks'][0]
    for path in ('../secret.json','/etc/passwd','safe/../../secret.json','nested//file.json'):
        check.update(source='json_file',path=path)
        with pytest.raises(ValueError):validate_plan(p,1)
    check.update(source='result',path='',operator='min_count',expected=0)
    with pytest.raises(ValueError):validate_plan(p,1)
    assert not compare(True,{'operator':'number','expected':1,'tolerance':0})


def test_file_receipt_contains_original_bytes_and_empty_file_fails():
    c=contract('file')
    from app.product_assurance import digest
    for role in ('qa','financial_analyst'):
        c[role]['checks'][0].update(source='csv_file',path='report.csv',pointer='',operator='min_count',expected=1)
    c['digest']=digest({k:v for k,v in c.items() if k!='digest'})
    for raw,passed in ((b'total\n30\n',True),(b'total\n',False)):
        values=collected({'total':30})|{'csv_file:report.csv':{'data':base64.b64encode(raw).decode()}}
        r=evaluate(c,'deploy',values)
        assert r['passed']==passed
        assert base64.b64decode(r['outputs'][0]['data'])==raw


def test_explicit_unsupported_criterion_cannot_be_waived_by_passing_controls():
    from app.product_assurance import digest
    c=contract();c['qa']['unsupported_criteria']=[0]
    c['digest']=digest({k:v for k,v in c.items() if k!='digest'})
    r=evaluate(c,'check',collected({'total':30}))
    assert not r['passed']
    r['passed']=True
    for check in r['checks']:check['passed']=True
    assert not receipt_valid(c,r,'check')


def test_unloaded_rows_block_even_with_a_correct_total():
    from app.product_assurance import digest
    c=contract()
    c['qa']['checks'].append(c['qa']['checks'][0]|{'id':'rows','pointer':'/rows','operator':'min_count','expected':1})
    c['digest']=digest({k:v for k,v in c.items() if k!='digest'})
    assert not evaluate(c,'check',collected({'total':30,'rows':[]}))['passed']


def test_xlsx_cells_and_pptx_slides_are_parsed_without_executing_content():
    import io,zipfile
    from scripts.product_verifier import decode
    def zipped(files):
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w') as z:
            for name,value in files.items():z.writestr(name,value)
        return output.getvalue()
    sheet='<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c r="A1"><v>30</v></c></row></sheetData></worksheet>'
    assert decode(zipped({'xl/worksheets/sheet1.xml':sheet}),'xlsx_file')=={'sheet1':{'A1':'30'}}
    with pytest.raises(ValueError):decode(zipped({'xl/worksheets/sheet1.xml':sheet.replace('<v>30</v>','<f>10+20</f><v>30</v>')}),'xlsx_file')
    slide='<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:t>Итого 30 RUB</a:t></p:sld>'
    assert decode(zipped({'ppt/slides/slide1.xml':slide}),'pptx_file')=={'slides':['Итого 30 RUB']}
