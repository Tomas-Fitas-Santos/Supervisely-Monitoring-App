"""Single-process external HTTPS shell for the existing Supervisely dashboard.

Native app authentication cannot be reused on arbitrary hosting. ExternalShell
enforces real email login, same-origin actions, verified monitor identity, and
per-session SDK dashboard state isolation. Deploy exactly one Uvicorn worker.
"""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.encoders import jsonable_encoder
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from .external_auth import ExternalAuth, normal_email

COOKIE = '__Host-nightjar-session'
ACTIONS = frozenset({
    '/dashboard/action', '/setup/action', '/settings/action',
    '/attendance/action', '/logistics/action',
})
LOGIN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bird Annotatiathon — Monitor sign-in</title>
<style>body{font:16px system-ui;background:#f1f4f7;display:grid;place-items:center;
min-height:100vh;margin:0}main{background:white;border-radius:12px;padding:30px;
width:min(430px,90vw);box-shadow:0 4px 25px #0002}
input,button{display:block;width:100%;box-sizing:border-box;margin:12px 0;
padding:12px;border-radius:8px}button{background:#155bc0;color:white;border:0}
input{border:1px solid #a6b4c2}#notice{min-height:30px}</style></head>
<body><main><h1>Monitor sign-in</h1><p>Use the email registered in the
Annotatiathon Monitors sheet. A six-digit code will arrive by email.</p>
<label>Email<input id="email" type="email" autocomplete="email"></label>
<button id="request" type="button">Send sign-in code</button>
<label>Verification code<input id="code" inputmode="numeric"
autocomplete="one-time-code" maxlength="6" placeholder="000000"></label>
<button id="verify" type="button">Sign in</button><p id="notice" role="status"></p>
</main><script>
async function call(path,body){
 const notice=document.getElementById('notice');
 notice.textContent='Working…';
 try{
  const res=await fetch(path,{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const out=await res.json();
  if(!res.ok)throw new Error(out.message||'Request failed');
  notice.textContent=out.message||'Check your email.';
  if(out.ok&&path==='/auth/verify')location.href='/';
 }catch(e){notice.textContent=e.message||'Could not sign in.';}
}
document.getElementById('request').onclick=()=>call('/auth/request',{
 email:document.getElementById('email').value});
document.getElementById('verify').onclick=()=>call('/auth/verify',{
 email:document.getElementById('email').value,
 code:document.getElementById('code').value});
</script></body></html>"""


class ExternalShell:
    def __init__(self, application, dashboard, sessions):
        self.application = application
        self.dashboard = dashboard
        self.auth = ExternalAuth(sessions)
        self.public_origin = os.getenv('EXTERNAL_PUBLIC_ORIGIN', '').rstrip('/')
        origin = urlsplit(self.public_origin)
        if origin.scheme != 'https' or not origin.netloc or origin.path not in ('', '/'):
            raise RuntimeError('EXTERNAL_PUBLIC_ORIGIN must be an HTTPS origin without a path.')
        self.host = origin.netloc.lower()
        self.lock = asyncio.Lock()
        self.views = {}

    @staticmethod
    async def _reply(response, scope, receive, send):
        response.headers['Cache-Control'] = 'no-store'
        await response(scope, receive, send)

    async def _post_auth(self, path, request, scope, receive, send):
        try:
            if request.headers.get('content-length') and int(
                    request.headers['content-length']) > 4096:
                raise ValueError()
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError()
            email = normal_email(data.get('email'))
            if path == '/auth/request':
                self.auth.request_code(email)
                response = JSONResponse({
                    'ok': True,
                    'message': 'If this address is authorised, a sign-in code has been sent.'
                })
            elif path == '/auth/verify':
                token = self.auth.verify_code(email, data.get('code'))
                response = JSONResponse({'ok':True,'message':'Signed in.'})
                response.set_cookie(COOKIE, token, max_age=12*3600,
                                    httponly=True, secure=True, samesite='strict',
                                    path='/')
            else:
                response = JSONResponse({'ok':False}, status_code=404)
        except Exception:
            # Do not reveal whether an email is allowed, a code exists or
            # SMTP credentials have been configured.
            response = JSONResponse({'ok':False,
                'message':'Invalid code or email service unavailable. Try again.'},
                status_code=400)
        await self._reply(response,scope,receive,send)

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            return await self.application(scope, receive, send)
        if scope['type'] != 'http':
            return await self._reply(JSONResponse({'message':'Not supported'},status_code=403),
                                     scope,receive,send)
        path = scope.get('path', '')
        method = scope.get('method', 'GET').upper()
        request = Request(scope, receive)
        # Reverse proxies must preserve the external Host header. Never trust
        # client-supplied X-Forwarded-* as an authentication claim.
        if request.headers.get('host','').lower() != self.host:
            return await self._reply(JSONResponse({'message':'Invalid host'},status_code=403),
                                     scope,receive,send)
        if method == 'GET' and path.startswith('/static/'):
            return await self.application(scope, receive, send)
        if method == 'GET' and path == '/healthz':
            return await self.application(scope, receive, send)
        if method == 'POST' and request.headers.get('origin') != self.public_origin:
            return await self._reply(JSONResponse({'message':'Origin not allowed'},
                                       status_code=403),scope,receive,send)
        if method == 'POST' and path in ('/auth/request','/auth/verify'):
            return await self._post_auth(path,request,scope,receive,send)
        token = request.cookies.get(COOKIE,'')
        email = self.auth.session_email(token)
        if not email:
            if method == 'GET' and path == '/':
                return await self._reply(HTMLResponse(LOGIN_HTML,headers={
                    'Content-Security-Policy':
                    "default-src 'none'; style-src 'unsafe-inline'; "
                    "script-src 'unsafe-inline'; connect-src 'self'; "
                    "frame-ancestors 'none'; form-action 'none'"}),
                    scope,receive,send)
            return await self._reply(JSONResponse({'message':'Sign in required.'},
                                       status_code=401),scope,receive,send)
        key = hashlib.sha256(token.encode()).hexdigest()
        if path == '/auth/logout' and method == 'POST':
            self.auth.logout(token)
            self.views.pop(key,None)
            response = JSONResponse({'ok':True})
            response.delete_cookie(COOKIE, path='/')
            return await self._reply(response,scope,receive,send)
        if method == 'GET' and path == '/':
            text = (Path(__file__).parent/'static'/'local.html').read_text(encoding='utf-8')
            initial = json.dumps({'dashboard':self.dashboard.get_json_data()})
            text = text.replace('__INITIAL_DATA__',
                                json.dumps(self.dashboard.get_json_data()).replace('<','\\u003c'))
            response=HTMLResponse(text,headers={
                'Cache-Control':'no-store',
                'Content-Security-Policy':
                    "default-src 'self' data: blob:; "
                    "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
                    "style-src 'self' 'unsafe-inline'; "
                    "img-src 'self' data: blob:; connect-src 'self'; "
                    "frame-ancestors 'none'"})
            return await self._reply(response,scope,receive,send)
        if path == '/sly/data' and method == 'POST':
            data = self.views.get(key) or self.dashboard.get_json_data()
            return await self._reply(JSONResponse({'dashboard':jsonable_encoder(data)}),
                                     scope,receive,send)
        if method != 'POST' or path not in ACTIONS:
            return await self._reply(JSONResponse({'message':'Not found'},
                                       status_code=404),scope,receive,send)
        declared = request.headers.get('content-length')
        if declared and int(declared) > 12 * 1024 * 1024:
            return await self._reply(JSONResponse({'message':'Request too large'},
                                       status_code=413),scope,receive,send)
        scope.setdefault('state', {})['external_email'] = email
        # The Supervisely SDK dashboard model is process-global. Isolate the
        # user-specific view around each action; never expose its shared data
        # endpoint or websocket to external clients.
        from supervisely.app import DataJson
        async with self.lock:
            dashboard = DataJson()['dashboard']
            dashboard.clear()
            dashboard.update(copy.deepcopy(
                self.views.get(key) or self.dashboard.get_json_data()))
            try:
                await self.application(scope, receive, send)
            finally:
                self.views[key] = copy.deepcopy(dict(DataJson()['dashboard']))
