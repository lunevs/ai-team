import json
from http.server import BaseHTTPRequestHandler,HTTPServer
from data.value import base_value


def result():
    return {'value':base_value(),'unit':'synthetic-check'}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/result': payload=result()
        elif self.path == '/health': payload={'status':'ok'}
        else:
            self.send_response(404);self.end_headers();return
        body=json.dumps(payload).encode();self.send_response(200)
        self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)))
        self.end_headers();self.wfile.write(body)
    def log_message(self,*args):pass


if __name__=='__main__':HTTPServer(('0.0.0.0',8000),Handler).serve_forever()
