"""Local frontend with bundled Vue; hosted sessions retain the native SDK shell."""
import json
from pathlib import Path
from starlette.responses import HTMLResponse


class LocalShell:
    def __init__(self, application, dashboard):
        self.application, self.dashboard = application, dashboard

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http' and scope['method'] == 'GET' and scope['path'] == '/':
            text = (Path(__file__).parent/'static'/'local.html').read_text()
            text = text.replace('__INITIAL_DATA__', json.dumps(self.dashboard.get_json_data()).replace('<', '\\u003c'))
            response = HTMLResponse(text, headers={'Cache-Control':'no-store',
                'Content-Security-Policy': "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'"})
            await response(scope, receive, send)
        else:
            await self.application(scope, receive, send)
