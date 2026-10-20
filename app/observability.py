import json
import logging
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

trace.set_tracer_provider(TracerProvider())
tracer=trace.get_tracer('ai-team')
logger=logging.getLogger('ai-team')
logger.setLevel(logging.INFO)
handler=logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(message)s'))
logger.addHandler(handler)
logging.getLogger('httpx').setLevel(logging.WARNING)

def log(event,**fields):
    # Only explicitly selected metadata; never exception messages or request bodies.
    logger.info(json.dumps({'event':event,**fields},ensure_ascii=False))
