"""Local frontend with bundled Vue; hosted sessions retain the native SDK shell."""
import json
from http.cookies import SimpleCookie
from pathlib import Path
from starlette.responses import HTMLResponse, JSONResponse


class LocalShell:
    def __init__(self, application, dashboard, session_lookup=None):
        self.application, self.dashboard = application, dashboard
        self.session_lookup = session_lookup

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
        if self.session_lookup and scope['type'] in ('http', 'websocket'):
            cookie = SimpleCookie()
            try:
                cookie.load(dict(scope.get('headers', [])).get(b'cookie', b'').decode())
                secret = cookie['nightjar_session'].value if 'nightjar_session' in cookie else ''
            except Exception:
                secret = ''
            row = self.session_lookup(secret)
            protected = scope['type'] == 'websocket' or scope['path'].startswith(('/sly/', '/setup/', '/dashboard/', '/settings/', '/attendance/'))
            if protected and not row:
                if scope['type'] == 'websocket':
                    await send({'type': 'websocket.close', 'code': 1008})
                else:
                    await JSONResponse({'message': 'Sign in to continue.'}, status_code=401)(scope, receive, send)
                return
            if row:
                # The SDK partitions widget models by this server-derived ID.
                # Never trust a browser-supplied ID to select another model.
                scope = dict(scope)
                scope['headers'] = [(k, v) for k, v in scope.get('headers', []) if k.lower() != b'x-user-id']
                scope['headers'].append((b'x-user-id', str(int(row.id[:15], 16)).encode()))
                from urllib.parse import parse_qsl, urlencode
                scope['query_string'] = urlencode([(k, v) for k, v in parse_qsl(scope.get('query_string', b'').decode()) if k != 'userId']).encode()
            # The local frontend uses HTTP; do not expose the SDK websocket's
            # separate query-based model selection to local browser sessions.
            if scope['type'] == 'websocket':
                await send({'type': 'websocket.close', 'code': 1008})
                return
        if scope['type'] == 'http' and scope['method'] == 'GET' and scope['path'] == '/':
            text = (Path(__file__).parent/'static'/'local.html').read_text()
            initial = self.dashboard.get_json_data()
            initial['connection']['local'] = True
            text = text.replace('__INITIAL_DATA__', json.dumps(initial).replace('<', '\\u003c'))
            response = HTMLResponse(text, headers={'Cache-Control':'no-store',
                'Content-Security-Policy': "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'"})
            await response(scope, receive, send)
        else:
            await self.application(scope, receive, send)
