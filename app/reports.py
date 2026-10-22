"""Fill the reviewed Excel template; retain formulas, styles and explicit NULLs."""
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zipfile import ZipFile
from xml.dom import minidom

METRICS=('balance','dpd30_balance','dpd90_balance','npl90_ratio','interest_income')
NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
STYLE={'navy':'#183153','blue':'#376CA6','background':'#F0F4F8','income':'#186A3B',
       'null':'—','currency':'RUB','money_decimals':2}

def monthly(rows):
    result=[]
    for month in sorted({r['month'] for r in rows}):
        parts=[r for r in rows if r['month']==month]
        item={'month':month}
        for name in METRICS:
            if name=='npl90_ratio':continue
            values=[Decimal(str(r[name])) for r in parts if r[name] is not None]
            item[name]=str(sum(values)) if values else None
        b,n=item['balance'],item['dpd90_balance']
        item['npl90_ratio']=str((Decimal(n)/Decimal(b)).quantize(Decimal('.000001'),rounding=ROUND_HALF_UP)) if b is not None and n is not None and Decimal(b)!=0 else None
        result.append(item)
    return result

def _cell(doc,address,value,formula=False):
    cell=next(c for c in doc.getElementsByTagNameNS(NS,'c') if c.getAttribute('r')==address)
    for child in list(cell.childNodes):
        if child.nodeType==child.ELEMENT_NODE and (not formula or child.localName!='f'):
            cell.removeChild(child)
    if cell.hasAttribute('t'):cell.removeAttribute('t')
    if value is None:
        if formula:
            cell.setAttribute('t','str');cell.appendChild(doc.createElementNS(NS,'v'))
    elif isinstance(value,str) and (address.startswith('A') or not _numeric(value)):
        if formula:
            cell.setAttribute('t','str');v=doc.createElementNS(NS,'v');v.appendChild(doc.createTextNode(value));cell.appendChild(v)
        else:
            cell.setAttribute('t','inlineStr');inline=doc.createElementNS(NS,'is');text=doc.createElementNS(NS,'t')
            text.appendChild(doc.createTextNode(value));inline.appendChild(text);cell.appendChild(inline)
    else:
        v=doc.createElementNS(NS,'v');v.appendChild(doc.createTextNode(str(value)));cell.appendChild(v)

def _numeric(value):
    try:return Decimal(value).is_finite()
    except Exception:return False

def create_workbook(rows,metadata,destination):
    from app.qa import check_rows
    if not check_rows(rows)['passed']:raise ValueError('Cannot report unverified results')
    ordered=sorted(rows,key=lambda r:(r['month'],int(r['product_id']),r['region']))
    template=Path(__file__).parent/'templates'/'credit_report.xlsx'
    with ZipFile(template) as source:
        files={i.filename:(i,source.read(i.filename)) for i in source.infolist()}
    summary=minidom.parseString(files['xl/worksheets/sheet1.xml'][1])
    data=minidom.parseString(files['xl/worksheets/sheet2.xml'][1])
    for document in [summary,data]:document.documentElement.setAttribute('xmlns',NS)
    for index,row in enumerate(ordered,5):
        for col,value in zip('ABCDEFGH',[row['month'],int(row['product_id']),row['region']]+[row[k] for k in METRICS]):
            _cell(data,f'{col}{index}',value)
    for index,row in enumerate(monthly(ordered),6):
        for col,key in [('B','balance'),('C','dpd30_balance'),('D','dpd90_balance'),('E','npl90_ratio'),('F','interest_income')]:
            _cell(summary,f'{col}{index}',row[key],formula=True)
    for index,key in enumerate(['dataset','checksum','sql_digest','story_id','revision','created_at'],17):
        _cell(summary,f'B{index}',str(metadata[key]))
    files['xl/worksheets/sheet1.xml']=(files['xl/worksheets/sheet1.xml'][0],summary.toxml(encoding='UTF-8'))
    files['xl/worksheets/sheet2.xml']=(files['xl/worksheets/sheet2.xml'][0],data.toxml(encoding='UTF-8'))
    workbook=minidom.parseString(files['xl/workbook.xml'][1])
    calc=workbook.getElementsByTagNameNS(NS,'calcPr')
    if calc:
        calc[0].setAttribute('fullCalcOnLoad','1');calc[0].setAttribute('forceFullCalc','1')
        files['xl/workbook.xml']=(files['xl/workbook.xml'][0],workbook.toxml(encoding='UTF-8'))
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    with ZipFile(destination,'w') as target:
        for info,content in files.values():target.writestr(info,content)
    return destination
