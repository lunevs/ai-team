"""Real structured-model contract smoke on synthetic material; no user story/approval."""
import json
from types import SimpleNamespace
from app.config import settings
from app.providers import OpenRouterProvider
from app.product_contract import AcceptancePlan,ProductReview
from app.product_assurance import validate_plan
from app.product_policy import build_package,inspect_package


def main():
    if not settings.llm_key:return {'status':'skipped','reason':'OpenRouter key not configured'}
    provider=OpenRouterProvider();usage=[];checks=[]
    def call(role,schema,context):
        result,used=provider.generate_structured(role,json.dumps(context,ensure_ascii=False),schema);usage.append(used);return result
    context={'story':'Техническая synthetic fixture: вернуть сумму 10 + 20 = 30 RUB в JSON /result.total; не загружать внешние данные.',
        'criteria':['Итог равен 30 RUB'],
        'instruction':'Независимо от реализации задай AcceptancePlan с одной числовой проверкой: criterion=0, source=result,path пустой,pointer=/total,operator=number,expected=30,tolerance=0,basis с независимым расчётом 10+20. not_applicable_reason пустой. description и basis минимум 10 символов. Все поля схемы обязательны.'}
    for role in ('qa','financial_analyst'):
        plan=call(role,AcceptancePlan,context);validate_plan(plan.model_dump(),1)
        assert plan.checks[0].operator=='number' and str(plan.checks[0].expected)=='30'
        checks.append(role+'_independent_plan_schema')
    bad={'story':context['story'],'solution':{'files':{'app.py':'def result(): return {"total":31,"unit":"RUB"}'}},
         'checks':{'evidence':{'passed':False,'actual':31,'expected':30}},
         'instruction':'Независимое ревью: approved=false, конкретное замечание о неверной сумме; факт 31 вместо независимых 30 подтверждён оператором. Не заявляй самостоятельный запуск.'}
    for role in ('qa','financial_analyst'):
        review=call(role,ProductReview,bad);assert not review.approved and review.findings
        checks.append(role+'_rejects_wrong_numeric_result')
    solution=SimpleNamespace(files={'app.py':'def result(): return {"total":30,"unit":"RUB"}\n','tests/test_product.py':'import unittest\nclass Test(unittest.TestCase):\n def test_sum(self):self.assertEqual(10+20,30)\n'},kind='job',title='Synthetic full package review',output_type='scalar',needs_database=False,architecture='Synthetic stdlib job with isolated data volume.',operating_notes='No external data or credentials. Read /data/result.json after job completion.')
    package,_=build_package(solution,'p-f11e00000002','1.0.1','synthetic',1);_,files=inspect_package(package)
    review=call('system_analyst',ProductReview,{'story':context['story'],'solution':{'files':solution.files,'full_package':{k:v.decode() for k,v in files.items()}},
        'instruction':'Проверь полный фактический пакет. Если есть дефекты, укажи их. Dockerfile, docker-compose.yml, runtime-requirements.lock и install.md поставлены платформой и представлены в full_package. Не заявляй их отсутствия из-за отсутствия в авторских files. Не требуется одобрение при наличии других замечаний.'})
    assert not any(('отсутств' in f.lower() or 'не предостав' in f.lower()) and 'dockerfile' in f.lower() for f in review.findings)
    checks.append('full_package_review_no_false_missing_dockerfile')
    return {'status':'passed','scope':'Real OpenRouter, synthetic schemas/reviews only; not business acceptance or GitHub review','checks':checks,'usage':usage,'architecture_findings':review.findings}

if __name__=='__main__':print('STAGE11_REAL_RESULT='+json.dumps(main(),ensure_ascii=False))
