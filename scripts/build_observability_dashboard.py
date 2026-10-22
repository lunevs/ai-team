"""Reproducible Grafana provisioning, no external plugins or secrets."""
import json
from pathlib import Path

panels=[]
def panel(title,type_,source,targets,x,y,w=12,h=8,**extra):
    ds_type={'ai-team-prometheus':'prometheus','ai-team-loki':'loki','ai-team-tempo':'tempo'}[source]
    item={'id':len(panels)+1,'title':title,'type':type_,'datasource':{'uid':source,'type':ds_type},
          'gridPos':{'x':x,'y':y,'w':w,'h':h},'targets':targets,**extra}
    panels.append(item);return item
def prom(expression,legend=''):
    return [{'refId':'A','expr':expression,'legendFormat':legend}]
def stat(title,expr,x,unit='short'):
    panel(title,'stat','ai-team-prometheus',prom(expr),x,0,w=6,h=4,
          fieldConfig={'defaults':{'unit':unit},'overrides':[]},options={'reduceOptions':{'calcs':['lastNotNull'],'fields':'','values':False}})

selector='backend=~"$backend"'
stat('Истории DONE · все backend','sum(ai_team_stories{status="DONE"}) or vector(0)',0)
stat('LLM-вызовы · весь журнал',f'sum(ai_team_llm_calls{{{selector}}}) or vector(0)',6)
stat('Расход USD · фактический + резерв',f'sum(ai_team_llm_cost_usd{{{selector}}}) or vector(0)',12,'currencyUSD')
stat('Доля ошибок LLM · весь журнал',f'(sum(ai_team_llm_calls{{{selector},state="failed"}}) or vector(0)) / clamp_min((sum(ai_team_llm_calls{{{selector}}}) or vector(0)),1)',18,'percentunit')
panel('Токены · фактические и зарезервированные','timeseries','ai-team-prometheus',
      prom(f'sum by (accounting) (ai_team_llm_tokens{{{selector}}})','{{accounting}}'),0,4,
      description='Снимок долговечного журнала. Резерв учитывается только без actual usage; перезапуск не прибавляет расход.',fieldConfig={'defaults':{'unit':'short'},'overrides':[]})
panel('USD · фактические и зарезервированные','timeseries','ai-team-prometheus',
      prom(f'sum by (accounting) (ai_team_llm_cost_usd{{{selector}}})','{{accounting}}'),12,4,
      fieldConfig={'defaults':{'unit':'currencyUSD'},'overrides':[]})
panel('Бюджеты последних активных историй','bargauge','ai-team-prometheus',
      prom(f'topk(10,ai_team_story_budget_ratio{{story_id=~"$story_id",{selector}}})','{{story_id}} / {{dimension}}'),0,12,
      fieldConfig={'defaults':{'unit':'percentunit','min':0,'max':1,'thresholds':{'mode':'absolute','steps':[{'color':'green','value':None},{'color':'orange','value':.8},{'color':'red','value':1}]}},'overrides':[]},
      options={'orientation':'horizontal','displayMode':'gradient','reduceOptions':{'calcs':['lastNotNull'],'fields':'','values':False}})
panel('LLM p95 · весь журнал, успешные вызовы','timeseries','ai-team-prometheus',
      prom(f'ai_team_llm_latency_seconds{{{selector},state="completed",quantile="0.95"}}','{{role}} / {{backend}}'),12,12,
      fieldConfig={'defaults':{'unit':'s'},'overrides':[]})
panel('Длительность операций p95 · выбранное окно и история','timeseries','ai-team-loki',
      [{'refId':'A','expr':'quantile_over_time(0.95, {service_name=~"ai-team-.*"} | json | event="operation.completed" | story_id=~"$story_id" | unwrap latency_ms | __error__="" [$__interval]) by (stage)','legendFormat':'{{stage}}'}],0,20,
      fieldConfig={'defaults':{'unit':'ms'},'overrides':[]})
panel('QA · сохранённые попытки','timeseries','ai-team-prometheus',prom('ai_team_qa_attempts','{{result}}'),12,20,
      description='По execution_attempts, без повторного счёта при возобновлении workflow.')
panel('Предупреждения и критические лимиты','table','ai-team-prometheus',
      [{'refId':'A','expr':'ALERTS{alertstate="firing"}','instant':True,'format':'table'}],0,28,
      description='Prometheus rules: 80% за 1 минуту; 100% за 15 секунд; недоступность ledger/Collector. Внешние уведомления не настроены.')
panel('Состояния историй · весь журнал','timeseries','ai-team-prometheus',
      prom('sum by (status) (ai_team_stories)','{{status}}'),12,28)
panel('Логи истории · TraceID открывает Tempo','logs','ai-team-loki',
      [{'refId':'A','expr':'{service_name=~"ai-team-.*"} | json | story_id=~"$story_id"','queryType':'range'}],0,36,w=24,h=12,
      options={'showTime':True,'showLabels':False,'wrapLogMessage':True,'sortOrder':'Descending','dedupStrategy':'none'},
      description='Только metadata. Введите UUID истории сверху. Поле TraceID связано с Tempo; обратная ссылка Tempo → Loki настроена в datasource.')
panel('Как читать показатели','text','ai-team-prometheus',[],0,48,w=24,h=4,
      options={'mode':'markdown','content':'**Story UUID / regex** фильтрует бюджет, операции и логи. Backend фильтрует метрики LLM.\n\nGauge-показатели берутся из всего committed ledger; выбранный период показывает их изменение, а не расходы только за период. Actual и reserved не перекрываются. Статусы включают служебные тестовые истории. Traces/logs хранятся 48 часов и не заменяют журнал PostgreSQL.\n\nОткройте **TraceID** у строки лога для просмотра spans в Tempo. Внешние уведомления отсутствуют.'})

dashboard={'uid':'ai-team-operations','title':'AI Team · Delivery operations','schemaVersion':41,'version':1,'editable':False,
           'timezone':'browser','refresh':'15s','time':{'from':'now-1h','to':'now'},'tags':['ai-team','observability'],
           'templating':{'list':[
               {'name':'story_id','label':'Story UUID / regex','type':'textbox','query':'.*','current':{'text':'.*','value':'.*'}},
               {'name':'backend','label':'Backend · метрики','type':'query','datasource':{'uid':'ai-team-prometheus','type':'prometheus'},
                'query':'label_values(ai_team_llm_calls, backend)','refresh':1,'includeAll':True,'allValue':'.*',
                'current':{'text':'All','value':'$__all'},'multi':False}]},'panels':panels}
path=Path('infra/observability/grafana/dashboards/ai-team.json')
path.parent.mkdir(parents=True,exist_ok=True)
path.write_text(json.dumps(dashboard,ensure_ascii=False,indent=2)+'\n')
print('Grafana dashboard generated:',path)
