"""Scoped structured review/triage smoke; run only in a disposable test DB."""
import json,uuid,time
from app import db
from app.config import settings
from app.schemas import StoryInput
from app.providers import generate,RetryableProviderError

def request(*args,**kwargs):
    for attempt in range(3):
        try:return generate(*args,**kwargs)
        except RetryableProviderError:
            if attempt==2:raise
            time.sleep(2**attempt)
from app.rework_contract import SourceReview,Triage,validate_triage,Finding


def main():
    if not settings.llm_key:return {'status':'skipped','reason':'OpenRouter key not configured'}
    story,_=db.create_story(StoryInput(text='Техническая проверка новых структурированных ревью на синтетическом коде',synthetic_only=True,delivery_mode='product'),'stage9-real-'+str(uuid.uuid4()))
    sid=story['id']
    review=request(sid,'qa',SourceReview,{'story':'Функция result должна вернуть целое число 42.','task':{'title':'Реализовать result','acceptance_criteria':['result() == 42']},
        'files':{'app.py':'def result():\n    return 41\n','tests/test_result.py':'import unittest\nfrom app import result\nclass Check(unittest.TestCase):\n def test_result(self):self.assertEqual(result(),42)\n'},
        'checks':{'passed':False,'evidence':'AssertionError: 41 != 42'},'instruction':'Проверь несоответствие, верни SourceReview: approved=false и конкретное замечание со всеми полями. Не утверждай запуск кода; подтверждение теста приведено как данные.'},result_role='stage9_real_review')
    assert not review.approved and review.findings
    finding=Finding(summary='Отсутствует Dockerfile в рабочих исходниках',criterion='Пакет должен устанавливаться независимо',evidence='В files пока только app.py и тесты',severity='minor',verification='Проверить доверенную упаковку')
    triage=request(sid,'system_analyst',Triage,{'findings':[finding.model_dump()],'platform_files':['Dockerfile','install.md','docker-compose.yml'],
        'instruction':'Известный факт: доверенная платформа добавляет перечисленные platform_files при упаковке, сейчас рассматриваются только рабочие исходники. Классифицируй замечание index=0: оно ошибочное, поскольку требует этот файл на неверном этапе. Обоснуй решение и укажи действие перепроверки пакета.'},result_role='stage9_real_triage')
    validate_triage([finding],triage)
    assert triage.findings[0].decision=='dismissed'
    with db.connection() as c:c.execute("UPDATE stories SET status='CANCELLED' WHERE id=%s",(sid,))
    return {'status':'passed','checks':['real_model_detects_wrong_value_with_evidence','real_model_dismisses_platform_file_false_positive'],'usage':db.usage(sid)}

if __name__=='__main__':print('REAL_REVIEW_RESULT='+json.dumps(main(),ensure_ascii=False))
