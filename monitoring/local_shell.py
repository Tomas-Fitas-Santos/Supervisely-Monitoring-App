"""Local frontend with bundled Vue; hosted sessions retain the native SDK shell."""
import json
from pathlib import Path
from starlette.responses import HTMLResponse, JSONResponse


class LocalShell:
    def __init__(self, application, dashboard):
        self.application, self.dashboard = application, dashboard

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            from urllib.parse import urlsplit
            headers = {key.decode().lower(): value.decode() for key, value in scope.get('headers', [])}
            try:
                hostname = urlsplit('//' + headers.get('host', '')).hostname
            except ValueError:
                hostname = None
            if hostname not in ('127.0.0.1', 'localhost', '::1'):
                await JSONResponse({'message':'Use a loopback address to open the local app.'}, status_code=403)(scope, receive, send)
                return
        if scope['type'] == 'http' and scope['method'] == 'POST':
            origin = headers.get('origin')
            expected = scope.get('scheme', 'http') + '://' + headers.get('host', '')
            if origin and origin.rstrip('/') != expected.rstrip('/'):
                await JSONResponse({'message':'Open the app on its own local address.'}, status_code=403)(scope, receive, send)
                return
        if scope['type'] == 'http' and scope['method'] == 'GET' and scope['path'] == '/':
            text = (Path(__file__).parent/'static'/'local.html').read_text()
            text = text.replace('__INITIAL_DATA__', json.dumps(self.dashboard.get_json_data()).replace('<', '\\u003c'))
            response = HTMLResponse(text, headers={'Cache-Control':'no-store',
                'Content-Security-Policy': "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'"})
            await response(scope, receive, send)
        else:
            await self.application(scope, receive, send)
