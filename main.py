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

    async def audited_fetch(self, credentials, kind):
        if credentials.get('provider') == 'wxy':
            return await self.owner.wxy.query(credentials['selection'])
        return await super().audited_fetch(credentials, kind)

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
        self.wxy=None; self.wxy_scheduler=None
        self.qq_binding={}
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
        if (self.folder/'wxy-session.enc').exists():
            from .wxy_session import WxySession
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            self.wxy=WxySession(self.folder,self.cipher)
            self.wxy_scheduler=AsyncIOScheduler(timezone=CHINA)
            self.wxy_scheduler.add_job(self.wxy_refresh,'cron',minute='0,30',id='wxy-session-check',
                                       max_instances=1,coalesce=True,misfire_grace_time=120)
            self.wxy_scheduler.start()
        self.task=asyncio.create_task(self.run())

    async def wxy_refresh(self):
        try:
            await self.wxy.query()
            for worker in self.workers.values():
                if worker.credentials().get('provider')=='wxy' and worker.state('suspended')=='auth':
                    worker.set_state('suspended','')
                    with worker.db:
                        worker.db.execute("UPDATE checks SET status='retry',attempts=0 WHERE day=? AND status='auth'",(datetime.now(CHINA).date().isoformat(),))
        except Exception as exc:
            logger.warning('WXY session check failed: %s; shared session needs checking',type(exc).__name__)

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
                if item['credentials'].get('provider')=='wxy':
                    if not self.wxy: raise RuntimeError('WXY session not configured')
                    snapshot=await self.wxy.query(item['credentials']['selection'])
                else:
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
                saved_credentials=json.loads(self.cipher.decrypt(path.read_bytes()))
                config={**dict(self.config),'qq':binding['qq'],'provider_kind':saved_credentials.get('provider','legacy')}
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
                try:
                    await worker.check(now)
                    await worker.keepalive(now)
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
        if await self.handle_dorm_binding(event): return
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

    async def handle_dorm_binding(self,event):
        qq=str(event.get_sender_id());text=event.message_str.strip()
        pending=self.qq_binding.get(qq)
        if pending and pending['expires']<time.time():
            self.qq_binding.pop(qq,None);pending=None
        if text!='绑定宿舍' and not pending:return False
        if text in ('电费查询','电费状态'):return False
        event.stop_event()
        async def say(message):await event.send(event.plain_result(message))
        try:
            if text=='绑定宿舍':
                result=await self.bridge_api('qq/start',{'qq':qq})
                self.qq_binding[qq]={'id':result['id'],'expires':time.time()+result['expires_in'],'submitted':False}
                await say('绑定宿舍与电费提醒\n请在 10 分钟内发送宿舍号，例如：天佑斋 012001（1 栋 2 楼 1 号，格式示例）。\n支持天佑斋及鸿哲斋 2、3 栋。10 楼示例：鸿哲斋 0210001。\n发送宿舍号后会核对并完成绑定，与网站同步。每天北京时间 09:00 查询，低于 20 元时 QQ 私聊提醒，每天最多一次。\n※ 网站与 QQ 共用每个自然月一次的成功绑定额度；查询失败不扣次数。\n发送“取消绑定”可退出本次操作，不会解除已有绑定。')
                return True
            if text=='取消绑定' and not pending['submitted']:
                self.qq_binding.pop(qq,None)
                await say('已退出本次绑定，原有宿舍绑定保持不变。');return True
            if pending['submitted']:
                await say('宿舍正在核对和保存，请稍候。可到网站账号设置查看结果。');return True
            await self.bridge_api('qq/room',{'qq':qq,'id':pending['id'],'room':text})
            pending['submitted']=True
            await say('正在核对宿舍并同步网站，请稍候。')
            for _ in range(40):
                result=await self.bridge_api('qq/status',{'qq':qq,'id':pending['id']})
                if result['state']=='done':
                    self.qq_binding.pop(qq,None)
                    snapshot=result['snapshot']
                    await say('宿舍绑定成功，已同步网站。\n'+snapshot['room']+'\n当前余额：'+snapshot['total']+' 元\n查询时间：'+snapshot['updated']+'\n每天 09:00 查询，低于 20 元时提醒。发送“电费查询”可查看最近结果。')
                    return True
                if result['state']=='failed':
                    self.qq_binding.pop(qq,None)
                    await say('查询未成功，未修改原绑定，也未扣除本月次数。请核对宿舍号后重新发送“绑定宿舍”。');return True
                await asyncio.sleep(2)
            self.qq_binding.pop(qq,None)
            await say('后台仍在处理，请到 你所使用的网站账号设置查看最终结果；暂时不要重复提交。')
        except httpx.HTTPStatusError as exc:
            try: detail=exc.response.json().get('detail')
            except Exception: detail=None
            if exc.response.status_code!=400:self.qq_binding.pop(qq,None)
            await say(detail if isinstance(detail,str) else '绑定服务暂不可用，请稍后重试。')
        except Exception:
            self.qq_binding.pop(qq,None)
            await say('暂未获取到最终结果，请到 你所使用的网站账号设置核对；如未完成，请重新发送“绑定宿舍”。')
        return True

    async def terminate(self):
        if self.wxy_scheduler: self.wxy_scheduler.shutdown(wait=False)
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError): await self.task
        for worker in self.workers.values(): await worker.terminate()
        if self.wxy:
            async with self.wxy.lock: await self.wxy.close()
        if self.bridge: await self.bridge.aclose()
