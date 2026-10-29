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
