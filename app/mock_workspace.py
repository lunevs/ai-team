"""Deterministic test-only source tools; never selected for an OpenRouter story."""
from pathlib import Path
from app.workspace_contract import WorkspaceAction


def action(context,role):
    task=context['task']['key'];step=context['sequence'];files={f['path']:f for f in context['files']}
    root=Path(__file__).with_name('product_templates')
    def item(operation,**values):return WorkspaceAction(operation=operation,summary='Подготовлен и проверяем результат текущей синтетической задачи',**values)
    def write(path,content):return item('write',path=path,content=content,expected_sha256=files.get(path,{}).get('sha256','new'))
    if task=='extend':
        if step==0:return item('list')
        if step==1:return item('search',content='def result')
        if step==2:return item('read',path='app.py')
        if step==3:return item('edit',path='app.py',expected_sha256=files['app.py']['sha256'],old_text='def result():',new_text="def extra_result():\n    return {'value':43,'unit':'synthetic-check'}\n\n\ndef result():")
        if step==4:return item('edit',path='app.py',expected_sha256=files['app.py']['sha256'],old_text="if self.path == '/result': payload=result()",new_text="if self.path == '/extra': payload=extra_result()\n        elif self.path == '/result': payload=result()")
        if step==5:return write('tests/test_extra.py',(root/'workspace_extra_test.py').read_text())
        if step==6:return item('check')
        return item('finish')
    if role=='data_developer':
        if step==0:return write('data/value.py','def base_value():\n    return 42\n')
        return item('finish')
    if role=='application_developer':
        if step==0:return item('read',path='data/value.py')
        if step==1:return write('app.py',(root/'workspace_app.py').read_text())
        if step==2:return write('tests/test_product.py',(root/'workspace_test.py').read_text())
        return item('finish')
    if role in ['qa','system_analyst']:
        if step==0:return item('read',path='app.py')
        return item('finish')
    if step==0:return write('docs/'+role+'.md','# Синтетическая спецификация\n\nКонтрольное значение 42, единица synthetic-check, независимая проверка результата.\n')
    return item('finish')
