"""Public-source fetch policy shared by research and the isolated product gateway.

No proxy environment, redirects, ambient credentials, arbitrary headers or private IPs.
DNS results are validated as a whole and the validated IP is pinned to the TLS socket.
"""
import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
from urllib.parse import urlsplit,parse_qsl

MAX_BYTES=8_000_000
FORMATS={'csv','json','xml','zip','xlsx'}

def source_definition(value):
    if set(value)!={'id','url','format'}:raise ValueError('Source requires id, url, format only')
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}',value['id']):raise ValueError('Invalid source id')
    if value['format'] not in FORMATS:raise ValueError('Unsupported source format')
    url=value['url']
    if not isinstance(url,str) or len(url)>2000 or any(ord(c)<33 or ord(c)>126 for c in url) or '\\' in url:raise ValueError('Invalid source URL')
    parsed=urlsplit(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.port not in (None,443):raise ValueError('Only public HTTPS on port 443')
    if any(k.lower() in {'token','access_token','api_key','apikey','password','secret','signature','key'} for k,v in parse_qsl(parsed.query)):
        raise ValueError('Credentials are forbidden in public source URLs')
    host=parsed.hostname
    if not re.fullmatch(r'[a-z0-9.-]+',host) or host.endswith('.') or '.' not in host or any(not p or len(p)>63 for p in host.split('.')):raise ValueError('Invalid source host')
    try:
        address=ipaddress.ip_address(host)
    except ValueError:address=None
    if address is not None:raise ValueError('Use a public DNS hostname, not a literal IP')
    if host.endswith(('.localhost','.local','.internal')):raise ValueError('Local source forbidden')
    return dict(value)


def sources_policy(values):
    if not isinstance(values,list) or len(values)>12:raise ValueError('Source limit')
    result=[source_definition(v) for v in values]
    if len({v['id'] for v in result})!=len(result):raise ValueError('Duplicate source id')
    return result


def public_addresses(host):
    addresses=sorted({row[4][0] for row in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)})
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):raise ValueError('Source resolves to a forbidden address')
    return addresses


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self,host,ip):
        super().__init__(host,timeout=20,context=ssl.create_default_context());self.ip=ip
    def connect(self):
        sock=socket.create_connection((self.ip,443),self.timeout)
        try:self.sock=self._context.wrap_socket(sock,server_hostname=self.host)
        except Exception:sock.close();raise


def fetch(source):
    source=source_definition(source);parts=urlsplit(source['url'])
    connection=PinnedHTTPS(parts.hostname,public_addresses(parts.hostname)[0])
    try:
        connection.request('GET',(parts.path or '/')+('?' +parts.query if parts.query else ''),headers={'User-Agent':'AI-Team-ProductSource/1','Accept-Encoding':'identity'})
        response=connection.getresponse()
        if response.status!=200:raise ValueError('Source HTTP status '+str(response.status)+'; redirects are forbidden')
        if response.getheader('Content-Encoding','identity')!='identity':raise ValueError('Encoded response forbidden')
        size=response.getheader('Content-Length')
        if size and int(size)>MAX_BYTES:raise ValueError('Source too large')
        import time
        deadline=time.monotonic()+40;chunks=[];total=0
        while True:
            if time.monotonic()>deadline:raise ValueError("Source download deadline exceeded")
            chunk=response.read1(min(65536,MAX_BYTES+1-total))
            if not chunk:break
            chunks.append(chunk);total+=len(chunk)
            if total>MAX_BYTES:raise ValueError("Source too large")
        content=b"".join(chunks)
        if len(content)>MAX_BYTES:raise ValueError('Source too large')
        return content,{'source_id':source['id'],'url':source['url'],'format':source['format'],'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest(),'content_type':response.getheader('Content-Type','')[:200]}
    finally:connection.close()


def serve():
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from pathlib import Path
    sources={s['id']:s for s in sources_policy(json.loads(Path('/policy/sources.json').read_text()))}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            key=self.path.removeprefix('/sources/') if self.path.startswith('/sources/') else ''
            if key not in sources:self.send_error(404);return
            try:data,info=fetch(sources[key])
            except Exception:self.send_error(502,'Approved source unavailable');return
            self.send_response(200);self.send_header('Content-Type',info['content_type'] or 'application/octet-stream');self.send_header('Content-Length',str(len(data)));self.send_header('X-Source-SHA256',info['sha256']);self.end_headers();self.wfile.write(data)
        def log_message(self,*args):pass # no URLs, data or credentials in logs
    # Bound concurrency as well as response sizes.
    class Server(ThreadingHTTPServer):
        request_queue_size=4
        def __init__(self,*args):
            import threading
            self.slots=threading.BoundedSemaphore(4);super().__init__(*args)
        def process_request(self,request,address):
            if not self.slots.acquire(False):request.close();return
            super().process_request(request,address)
        def process_request_thread(self,request,address):
            try:super().process_request_thread(request,address)
            finally:self.slots.release()
    Server(('0.0.0.0',8080),Handler).serve_forever()

if __name__=='__main__':serve()
