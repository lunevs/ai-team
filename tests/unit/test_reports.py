from zipfile import ZipFile
from xml.etree import ElementTree as ET
from app.reports import create_workbook,monthly
from app.synthetic import reference

def test_report_preserves_formulas_nulls_and_reproducible_bytes(tmp_path):
    rows=reference()
    assert monthly(rows)[0]['balance']=='15000.00'
    assert monthly(rows)[0]['npl90_ratio']=='0.266667'
    metadata=dict(dataset='credit-demo-v1',checksum='abc',sql_digest='def',story_id='uuid',revision=1,created_at='2026-10-22')
    first=create_workbook(rows,metadata,tmp_path/'a.xlsx')
    second=create_workbook(rows,metadata,tmp_path/'b.xlsx')
    assert first.read_bytes()==second.read_bytes()
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with ZipFile(first) as z:
        summary=ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
        cell=summary.find('.//s:c[@r="E6"]',ns)
        assert 'ROUND(D6/B6,6)' in cell.find('s:f',ns).text
        assert cell.find('s:v',ns).text=='0.266667'
        data=ET.fromstring(z.read('xl/worksheets/sheet2.xml'))
        values=[c.find('s:v',ns) for c in data.findall('.//s:c',ns) if c.attrib['r'].startswith('D') and int(c.attrib['r'][1:])>=5]
        assert any(v is None for v in values)
        assert any(v is not None and v.text=='0.00' for v in values)
