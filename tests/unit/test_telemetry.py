import io,json,logging
import pytest
from opentelemetry.sdk.trace import TracerProvider,SpanLimits
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from app.observability import SafeTracer,metadata,log,logger

def test_secret_payloads_and_exception_messages_are_not_recorded():
    secret='CANARY_DO_NOT_EXPORT_credential_or_customer_payload'
    exporter=InMemorySpanExporter()
    provider=TracerProvider(span_limits=SpanLimits(max_events=0))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer=SafeTracer(provider.get_tracer('test'))
    stream=io.StringIO();handler=logging.StreamHandler(stream);logger.addHandler(handler)
    try:
        with pytest.raises(ValueError):
            with tracer.start_as_current_span('story.run',attributes={'story_id':'11111111-1111-4111-8111-111111111111','prompt':secret,'db.statement':secret}):
                with tracer.start_as_current_span('tool.sql',attributes={'sql':secret}):
                    log('telemetry.probe',response=secret,token=secret,sql=secret,passed=True)
                    raise ValueError(secret)
    finally:logger.removeHandler(handler);provider.shutdown()
    spans=exporter.get_finished_spans()
    assert len(spans)==2
    assert all(not span.events and span.attributes['status']=='ERROR' for span in spans)
    assert all(span.attributes['story_id']=='11111111-1111-4111-8111-111111111111' for span in spans)
    assert secret not in stream.getvalue() and secret not in str([dict(s.attributes) for s in spans])
    records=[json.loads(line) for line in stream.getvalue().splitlines()]
    assert all(len(item['trace_id'])==32 for item in records)
    assert len({item['trace_id'] for item in records})==1

def test_metadata_rejects_unbounded_values_and_untrusted_dimensions():
    assert metadata({'role':['qa'],'backend':'secret','status':{},'tokens':10**500,'latency_ms':float('nan'),
                     'model':'secret token','prompt':'text','trace_id':'not-a-trace'})=={}
    assert metadata({'role':'qa','backend':'deterministic','passed':True,'attempt':2})=={'role':'qa','backend':'deterministic','passed':True,'attempt':2}
