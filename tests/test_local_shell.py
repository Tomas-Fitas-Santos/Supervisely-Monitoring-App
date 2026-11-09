import asyncio
from monitoring.local_shell import LocalShell


def test_foreign_browser_origin_cannot_read_models_or_trigger_local_actions():
    called=[]
    async def app(scope,receive,send):called.append(scope['path'])
    async def receive():return {'type':'http.request','body':b''}
    async def check(path):
        messages=[]
        async def send(message):messages.append(message)
        await LocalShell(app,None)({'type':'http','method':'POST','path':path,'scheme':'http',
            'headers':[(b'host',b'127.0.0.1:8000'),(b'origin',b'https://unrelated.example')]},receive,send)
        assert messages[0]['status']==403
    for path in ('/setup/action','/dashboard/action','/sly/data','/connection'):
        asyncio.run(check(path))
    assert called==[]


def test_same_origin_local_sdk_bridge_is_allowed():
    called=[]
    async def app(scope,receive,send):called.append(scope['path'])
    async def noop(*args):pass
    asyncio.run(LocalShell(app,None)({'type':'http','method':'POST','path':'/setup/action','scheme':'http',
        'headers':[(b'host',b'127.0.0.1:8000'),(b'origin',b'http://127.0.0.1:8000')]},noop,noop))
    assert called==['/setup/action']


def test_local_host_allowlist_blocks_rebinding_hosts_before_the_sdk():
    called=[];messages=[]
    async def app(scope,receive,send):called.append(scope['path'])
    async def receive():return {'type':'http.request','body':b''}
    async def send(message):messages.append(message)
    asyncio.run(LocalShell(app,None)({'type':'http','method':'POST','path':'/setup/action','scheme':'http',
        'headers':[(b'host',b'unrelated.example:8000'),(b'origin',b'http://unrelated.example:8000')]},receive,send))
    assert not called and messages[0]['status']==403


def test_cookie_identity_overrides_sdk_user_selection_and_requires_login():
    from types import SimpleNamespace as NS
    seen=[]
    async def app(scope,receive,send):seen.append(scope)
    async def noop(*args):pass
    shell=LocalShell(app,None,lambda secret:NS(id='abcdef0123456789') if secret=='valid' else None)
    scope={'type':'http','method':'POST','path':'/sly/data','scheme':'http',
        'query_string':b'userId=99', 'headers':[(b'host',b'127.0.0.1:8000'),
        (b'x-user-id',b'99'),(b'cookie',b'nightjar_session=valid')]}
    asyncio.run(shell(scope,noop,noop))
    assert dict(seen[0]['headers'])[b'x-user-id'] != b'99'
    assert seen[0]['query_string']==b''
    messages=[]
    async def send(message):messages.append(message)
    scope['headers']=[(b'host',b'127.0.0.1:8000'),(b'x-user-id',b'99')]
    asyncio.run(shell(scope,noop,send))
    assert len(seen)==1 and messages[0]['status']==401
