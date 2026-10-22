"""Closed metadata-only telemetry; no auto-instrumentation or exception payloads."""
from contextlib import contextmanager
import json
import logging
import math
import os
import re
import time
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider,SpanLimits
from opentelemetry.trace import Status,StatusCode

ROLES={'pm','system_analyst','financial_analyst','data_developer','application_developer','designer','qa'}
EVENTS={'api.error','tracker.pending','team.tracker_unavailable','llm.validation_failed','llm.failed',
        'llm.completed','worker.heartbeat.failed','story.checkpoint','story.failed','tracker.sync.pending',
        'tracker.stop.applied','tracker.watch.failed','worker.started','worker.loop.failed','worker.retry',
        'operation.completed','operation.failed','telemetry.probe','qa.completed','budget.exhausted'}
STATUSES={'OK','ERROR','NEW','ANALYSIS','PLAN_REVIEW','IN_PROGRESS','BLOCKED','FAILED','CANCELLED',
          'ACCEPTANCE','DONE','REQUESTED','DEVELOPING','QA','ARCHITECT_REVIEW','READY_TO_PUBLISH',
          'WAITING_GITHUB','PUBLISHED','BUSINESS_ACCEPTED','RELEASE_REVIEW','FINALIZING_RELEASE','ESCALATED'}

def metadata(fields):
    result={}
    for key,value in fields.items():
        if value is None:continue
        if key in {'story_id','worker_id','call_id'}:
            value=str(value)
            if re.fullmatch(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}',value):result[key]=value
        elif key=='trace_id':
            if isinstance(value,str) and re.fullmatch('[a-f0-9]{32}',value):result[key]=value
        elif key=='role' and isinstance(value,str) and value in ROLES:result[key]=value
        elif key=='backend' and isinstance(value,str) and value in {'mock','openrouter','deterministic','private'}:result[key]=value
        elif key=='status' and isinstance(value,str) and value in STATUSES:result[key]=value
        elif key=='model' and isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_./:-]{1,100}',value):result[key]=value
        elif key in {'error_type','finish_reason','stage'} and isinstance(value,str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]{0,63}',value):result[key]=value
        elif key=='validation_types' and isinstance(value,list):
            result[key]=[v for v in value[:10] if isinstance(v,str) and re.fullmatch('[a-z_]{1,64}',v)]
        elif key=='passed' and isinstance(value,bool):result[key]=value
        elif key in {'latency_ms','tokens','estimated_cost_usd','attempt','revision'} and isinstance(value,(int,float)) and 0<=value<=1e9 and math.isfinite(value):
            result[key]=value
    return result

service=os.getenv('OTEL_SERVICE_NAME','ai-team-worker')
if service not in {'ai-team-worker','ai-team-api'}:service='ai-team-worker'
provider=TracerProvider(resource=Resource({'service.name':service,'service.version':'0.5.0','deployment.environment.name':'local'}),
    span_limits=SpanLimits(max_attributes=16,max_events=0,max_links=0,max_attribute_length=128))
trace.set_tracer_provider(provider)
logger=logging.getLogger('ai-team')
logger.setLevel(logging.INFO);logger.propagate=False
if not logger.handlers:
    handler=logging.StreamHandler();handler.setFormatter(logging.Formatter('%(message)s'));logger.addHandler(handler)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('opentelemetry.exporter').setLevel(logging.CRITICAL)
logging.getLogger('opentelemetry.sdk').setLevel(logging.CRITICAL)
log_provider=None
endpoint=os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT','').rstrip('/')
if endpoint:
    if endpoint!='http://otel-collector:4318':raise ValueError('Only the local telemetry collector is enabled')
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.sdk._logs import LoggerProvider,LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint+'/v1/traces',timeout=2),
        max_queue_size=512,max_export_batch_size=32,schedule_delay_millis=1000,export_timeout_millis=2500))
    log_provider=LoggerProvider(resource=provider.resource)
    log_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint+'/v1/logs',timeout=2),
        max_queue_size=512,max_export_batch_size=32,schedule_delay_millis=1000,export_timeout_millis=2500))
    logger.addHandler(LoggingHandler(level=logging.INFO,logger_provider=log_provider))

def log(event,**fields):
    safe=metadata(fields)
    context=trace.get_current_span().get_span_context()
    if context.is_valid:safe['trace_id']=f'{context.trace_id:032x}'
    logger.info(json.dumps({'event':event if event in EVENTS else 'unknown.event',**safe},ensure_ascii=False,allow_nan=False))

class SafeTracer:
    def __init__(self,delegate):self.delegate=delegate

    @contextmanager
    def start_as_current_span(self,name,context=None,attributes=None):
        allowed={'story.run','llm.request','tracker.create','tool.sql','tool.github','tool.knowledge','artifact.publish'}|{'agent.'+r for r in ROLES}
        if name not in allowed:raise ValueError('Unknown telemetry operation')
        attributes=metadata(attributes or {})
        parent_attributes=getattr(trace.get_current_span(),'attributes',{}) or {}
        for key in ['story_id','role']:
            if key not in attributes and key in parent_attributes:attributes[key]=parent_attributes[key]
        started=time.monotonic();error=None
        with self.delegate.start_as_current_span(name,context=context,attributes=attributes,
                record_exception=False,set_status_on_exception=False) as active:
            try:yield active
            except BaseException as exc:
                error=type(exc).__name__;active.set_attribute('error_type',error)
                active.set_status(Status(StatusCode.ERROR));raise
            finally:
                failed=error is not None or getattr(active,'status',Status(StatusCode.UNSET)).status_code==StatusCode.ERROR
                elapsed=round((time.monotonic()-started)*1000,3)
                active.set_status(Status(StatusCode.ERROR if failed else StatusCode.OK))
                active.set_attribute('latency_ms',elapsed)
                active.set_attribute('status','ERROR' if failed else 'OK')
                fields=attributes|{'stage':name,'latency_ms':elapsed,'status':'ERROR' if failed else 'OK','error_type':error}
                log('operation.failed' if failed else 'operation.completed',**fields)

tracer=SafeTracer(trace.get_tracer('ai-team'))

@contextmanager
def story_trace(story,stage):
    import uuid
    parent=trace.NonRecordingSpan(trace.SpanContext(int(story['trace_id'],16),uuid.uuid4().int & ((1<<64)-1),
        is_remote=True,trace_flags=trace.TraceFlags(1)))
    with tracer.start_as_current_span('story.run',context=trace.set_span_in_context(parent),
            attributes={'story_id':str(story['id']),'revision':story['revision'],'stage':stage}) as active:yield active

def flush(timeout_ms=5000):
    spans=provider.force_flush(timeout_millis=timeout_ms)
    logs=log_provider.force_flush(timeout_millis=timeout_ms) if log_provider else True
    return spans and logs
