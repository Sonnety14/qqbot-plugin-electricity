"""Account-scoped bridge, encrypted credential versions and independent daily records."""
import asyncio
import contextlib
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
import httpx
from cryptography.fernet import Fernet
from astrbot.api import logger
from astrbot.api.star import Star, StarTools, register
from astrbot.api.event import filter
from .worker import ElectricityWorker, CHINA
from .provider import fetch_balance


def atomic_private(path, data):
    temporary=path.with_suffix('.pending')
    fd=os.open(temporary, os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary,path)
    os.chmod(path,0o600)


def identifiers(item):
    uid=str(item['user_id']); version=item.get('version',item.get('id',''))
    if not re.fullmatch(r'[1-9][0-9]*',uid) or not re.fullmatch(r'[a-f0-9]{32}',version):
        raise ValueError('Invalid binding identity')
    if not re.fullmatch(r'[1-9][0-9]{4,14}',str(item['qq'])): raise ValueError('Invalid QQ')
    return uid,version


class AccountWorker(ElectricityWorker):
    def credentials(self):
        return json.loads(self.owner.cipher.decrypt(self.credential_path.read_bytes()))

    async def send(self,text):
        # Recheck recipient ownership immediately before an outbound message.
        if not await self.owner.authorized(self.uid,self.version,str(self.config['qq'])):
            raise RuntimeError('Binding revoked')
        await super().send(text)


@register('astrbot_plugin_electricity','Electricity Bridge contributors','多用户每日电费查询与低余额提醒','0.8.3')
class Electricity(Star):
    def __init__(self, context, config):
        super().__init__(context)
        self.config=config; self.workers={}; self.bridge=None; self.task=None
        self.folder=Path(StarTools.get_data_dir('astrbot_plugin_electricity'))
        self.folder.mkdir(parents=True,exist_ok=True)
        key=self.folder/'accounts.key'
        if not key.exists():
            # Exclusive create: never replace a key on restart.
            fd=os.open(key,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as f: f.write(Fernet.generate_key()); f.flush(); os.fsync(f.fileno())
        os.chmod(key,0o600)
        self.cipher=Fernet(key.read_bytes())
        self.capacity=asyncio.Semaphore(4)

    async def initialize(self):
        from .configuration import bridge_settings
        if self.config.get('bridge_enabled',False):
            if not str(self.config.get('platform_id','')).strip():
                raise ValueError('QQ platform instance ID required')
            origin,headers=bridge_settings(self.config)
            self.bridge=httpx.AsyncClient(base_url=origin,headers=headers,timeout=30,trust_env=False)
        self.task=asyncio.create_task(self.run())

    async def bridge_api(self,path,data=None):
        if not self.bridge: raise RuntimeError('Website bridge required')
        r=await self.bridge.post('/comments/bridge/campus/electric/'+path,json=data or {})
        r.raise_for_status(); return r.json()

    async def authorized(self,uid,version,qq):
        items=(await self.bridge_api('bindings'))['items']
        return any(str(b['user_id'])==uid and b['version']==version and b['qq']==qq for b in items)

    def directory(self,item):
        uid,version=identifiers(item)
        folder=self.folder/'accounts'/uid
        folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        os.chmod(folder,0o700)
        return folder,folder/(version+'.enc')

    async def binding_tick(self):
        item=(await self.bridge_api('work'))['item']
        if item:
            result={'id':item['id'],'lease':item['lease'],'state':'failed'}
            try:
                identifiers(item)
                # Validation has its own cookie jar, never shared between accounts.
                async with httpx.AsyncClient(timeout=20,trust_env=False) as client:
                    snapshot=await fetch_balance(client,item['credentials'])
                now=datetime.now(CHINA)
                updated=datetime.fromisoformat(snapshot['updated']).replace(tzinfo=CHINA)
                if now-updated>timedelta(hours=48) or updated>now+timedelta(minutes=10): raise ValueError('Stale')
                if item['action']=='install':
                    folder,path=self.directory(item)
                    atomic_private(path,self.cipher.encrypt(json.dumps(item['credentials']).encode()))
                result.update(state='done' if item['action']=='install' else 'ready',snapshot=snapshot)
            except Exception as exc: logger.warning('Electricity binding: %s',type(exc).__name__)
            # Staged credentials become active only after the website commits this version.
            await self.bridge_api('result',result)
        bindings=(await self.bridge_api('bindings'))['items']
        active=set()
        for binding in bindings:
            uid,version=identifiers(binding)
            active.add(uid)
            folder,path=self.directory(binding)
            old=self.folder/'credentials.json'
            migrated=False
            if not path.exists() and old.exists() and binding['qq']==str(self.config.get('qq','')):
                credential=json.loads(old.read_bytes())
                if credential.get('binding_version')==version:
                    atomic_private(path,self.cipher.encrypt(json.dumps(credential).encode()))
                    if not (folder/'checks.sqlite3').exists() and (self.folder/'checks.sqlite3').exists():
                        with contextlib.closing(sqlite3.connect(self.folder/'checks.sqlite3')) as source, contextlib.closing(sqlite3.connect(folder/'checks.sqlite3')) as target:
                            source.backup(target)
                        migrated=True
            if not path.exists(): continue
            worker=self.workers.get(uid)
            if worker and (worker.version!=version or str(worker.config['qq'])!=binding['qq']):
                await worker.terminate(); del self.workers[uid]; worker=None
            if not worker:
                config={**dict(self.config),'qq':binding['qq']}
                worker=AccountWorker(self.context,config,folder=folder)
                worker.owner=self; worker.uid=uid; worker.version=version; worker.credential_path=path
                worker.client=httpx.AsyncClient(timeout=20,trust_env=False)
                if migrated: worker.set_state('active_version',version)
                if worker.state('active_version')!=version:
                    worker.set_state('suspended','')
                    with worker.db:
                        worker.db.execute("INSERT INTO checks VALUES (?,1,?,'ok',?,NULL) ON CONFLICT(day) DO UPDATE SET status='ok',snapshot=excluded.snapshot,attempted=excluded.attempted",(datetime.now(CHINA).date().isoformat(),time.time(),json.dumps(binding['snapshot'])))
                    worker.set_state('active_version',version)
                self.workers[uid]=worker
            if time.time()-worker.last_health>=60:
                await self.bridge_api('health',{'version':version,'state':worker.state('suspended') or 'active'})
                worker.last_health=time.time()
        for uid in set(self.workers)-active:
            await self.workers.pop(uid).terminate()

    async def check(self,now):
        async def one(worker):
            async with self.capacity:
                try: await worker.check(now)
                except Exception as exc: logger.warning('Electricity account check: %s',type(exc).__name__)
        await asyncio.gather(*(one(w) for w in self.workers.values()))

    async def run(self):
        while True:
            try:
                if self.bridge:
                    await self.binding_tick()
                    await self.check(datetime.now(CHINA))
            except Exception as exc: logger.warning('Electricity bridge: %s',type(exc).__name__)
            await asyncio.sleep(5)

    @filter.event_message_type(filter.EventMessageType.ALL,priority=2000)
    async def handle(self,event):
        if not event.is_private_chat() or event.get_platform_id()!=self.config.get('platform_id',''): return
        if event.message_str.strip() not in ('电费查询','电费状态'): return
        qq=str(event.get_sender_id())
        try:
            bindings=(await self.bridge_api('bindings'))['items']
            matches=[b for b in bindings if b['qq']==qq]
            if len(matches)!=1: return
            binding=matches[0]; worker=self.workers.get(str(binding['user_id']))
            if not worker or worker.version!=binding['version']: return
            await worker.handle(event)
        except Exception:
            event.stop_event()
            await event.send(event.plain_result('电费服务暂时不可用，请稍后再试。'))

    async def terminate(self):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError): await self.task
        for worker in self.workers.values(): await worker.terminate()
        if self.bridge: await self.bridge.aclose()
