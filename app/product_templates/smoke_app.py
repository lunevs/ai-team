"""Synthetic protocol fixture only; not a customer data product."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


def calculate():
    return {'value': 42, 'unit': 'synthetic-check'}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path not in ('/health', '/result'):
            self.send_error(404)
            return
        content=json.dumps({'status':'ok'} if self.path=='/health' else calculate()).encode()
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(content)))
        self.end_headers()
        self.wfile.write(content)


if __name__=='__main__':
    HTTPServer(('0.0.0.0',8000),Handler).serve_forever()
