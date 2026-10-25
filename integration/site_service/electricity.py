"""Multi-user electricity binding: short-lived tickets, encrypted staging, explicit confirmation."""
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import os
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from comment_service.store import digest
from .db import connect
from .identity import SESSION_COOKIE
from .electricity_policy import parse_room, initialize_quota, quota_available, consume_quota, next_binding_at, CHINA
from datetime import datetime
from .electricity_qq import initialize as initialize_qq, next_binding_at, quota_available, consume_quota

P = '/tools/campus/electric'
B = '/comments/bridge/campus/electric'


def build_electric_router(identity, origin, store, bridge_token, pilot_qq):
    router = APIRouter()
    wxy_mode = os.environ.get('ELECTRIC_BINDING_MODE') == 'wxy'
    bridge = hmac.new(bridge_token.encode(), b'campus-bindings-v1', hashlib.sha256).hexdigest()
    cipher = Fernet(base64.urlsafe_b64encode(hmac.new(bridge_token.encode(), b'electric-staging-v1', hashlib.sha256).digest()))
    with connect(identity.db) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS electric_jobs(
          user_id INTEGER PRIMARY KEY REFERENCES users(id), id TEXT UNIQUE NOT NULL, token_hash TEXT NOT NULL,
          auth_version INTEGER NOT NULL, qq TEXT NOT NULL, expires REAL NOT NULL, state TEXT NOT NULL,
          payload TEXT, snapshot TEXT, lease TEXT, lease_until REAL NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS electric_bindings(
          user_id INTEGER PRIMARY KEY REFERENCES users(id), qq TEXT NOT NULL, version TEXT NOT NULL,
          state TEXT NOT NULL, snapshot TEXT NOT NULL, updated REAL NOT NULL);
        ''')
        initialize_quota(db)
        db.execute('''CREATE TABLE IF NOT EXISTS electric_preferences (
          user_id INTEGER PRIMARY KEY REFERENCES users(id), alerts_enabled INTEGER NOT NULL DEFAULT 1
          CHECK(alerts_enabled IN (0,1)))''')
        if 'source' not in {r[1] for r in db.execute('PRAGMA table_info(electric_jobs)')}:
            db.execute("ALTER TABLE electric_jobs ADD COLUMN source TEXT NOT NULL DEFAULT 'web'")

    with connect(identity.db) as db:
        initialize_qq(db)

    def clean(db):
        db.execute("UPDATE electric_qq_jobs SET payload=NULL,token_hash='',state='expired' WHERE expires<=? AND state NOT IN ('done','expired')", (time.time(),))

    def session(request, write=False):
        s = identity.load_session(request.cookies.get(SESSION_COOKIE, ''))
        if not s: raise HTTPException(401, '请先登录')
        if not s.get('verified_at'): raise HTTPException(403, '请先验证邮箱')
        if write and (request.headers.get('origin') != origin or not secrets.compare_digest(request.headers.get('x-csrf-token', ''), s['csrf'])):
            raise HTTPException(403, '请刷新页面重试')
        request.state.user_id = s['id']
        return s

    def allowed(db, uid):
        row = db.execute('SELECT qq FROM campus_qq_bindings WHERE user_id=?', (uid,)).fetchone()
        return row['qq'] if row else None

    def internal(request):
        if not secrets.compare_digest(request.headers.get('authorization', ''), 'Bearer ' + bridge): raise HTTPException(401)

    def valid_job(db, job):
        return job and job['source']=='qq' and job['expires']>time.time() and db.execute(
            'SELECT 1 FROM electric_qq_accounts WHERE id=? AND qq=? AND auth_version=?',
            (job['user_id'],job['qq'],job['auth_version'])).fetchone()

    def qq_account(db, qq):
        if not re.fullmatch(r'[1-9][0-9]{4,14}',qq):raise HTTPException(400,'QQ 号无效')
        row=db.execute('SELECT id,auth_version FROM electric_qq_accounts WHERE qq=?',(qq,)).fetchone()
        if row:return row
        # Avoid reusing any legacy worker directory, even for accounts without a current binding.
        uid=db.execute('SELECT MAX(n)+1 FROM (SELECT COALESCE(MAX(id),0) n FROM users UNION ALL SELECT COALESCE(MAX(id),0) n FROM electric_qq_accounts)').fetchone()[0]
        db.execute('INSERT INTO electric_qq_accounts(id,qq) VALUES(?,?)',(uid,qq))
        return {'id':uid,'auth_version':1}

    def cooldown_error(db, uid):
        until=datetime.fromtimestamp(next_binding_at(db,uid),CHINA).strftime('%m-%d %H:%M')
        return HTTPException(409,'绑定成功后需间隔 24 小时；可于 '+until+'（北京时间）再次绑定。每个 QQ 独立计算冷却。')

    def alerts_enabled(db, uid):
        row=db.execute('SELECT alerts_enabled FROM electric_qq_preferences WHERE user_id=?',(uid,)).fetchone()
        return bool(row[0]) if row else True

    @router.post(B + '/qq/alerts')
    async def qq_alerts(request: Request):
        internal(request)
        data=await request.json();qq=str(data.get('qq',''))
        with connect(identity.db) as db:
            db.execute('BEGIN IMMEDIATE')
            account=qq_account(db,qq)
            binding=db.execute('SELECT 1 FROM electric_qq_bindings WHERE user_id=? AND qq=?',(account['id'],qq)).fetchone()
            if not binding: raise HTTPException(409,'尚未绑定宿舍，请先发送“绑定宿舍”。')
            enabled=not alerts_enabled(db,account['id'])
            db.execute('INSERT INTO electric_qq_preferences VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET alerts_enabled=excluded.alerts_enabled',(account['id'],int(enabled)))
        return {'alerts_enabled':enabled}

    @router.post(B + '/qq/start')
    async def qq_start(request: Request):
        internal(request)
        if not wxy_mode: raise HTTPException(503,'宿舍绑定暂未开放')
        data=await request.json();qq=str(data.get('qq',''))
        if not store.limit('electric-qq-start:'+qq,5): raise HTTPException(429,'请稍后重试')
        with connect(identity.db) as db:
            db.execute('BEGIN IMMEDIATE');clean(db)
            account=qq_account(db,qq)
            if not quota_available(db,account['id'],time.time()): raise cooldown_error(db,account['id'])
            old=db.execute('SELECT state FROM electric_qq_jobs WHERE user_id=?',(account['id'],)).fetchone()
            if old and old['state'] in ('validating','queued','processing'): raise HTTPException(409,'正在核对或保存，请稍候')
            ident=secrets.token_hex(16)
            db.execute('''INSERT OR REPLACE INTO electric_qq_jobs(user_id,id,token_hash,auth_version,qq,expires,state,source)
                VALUES(?,?,?,?,?,?,'qq_waiting','qq')''',(account['id'],ident,'',account['auth_version'],qq,time.time()+600))
        return {'id':ident,'expires_in':600}

    @router.post(B + '/qq/room')
    async def qq_room(request: Request):
        internal(request)
        data=await request.json();qq=str(data.get('qq',''))
        try: selection=parse_room(data.get('room'))
        except ValueError as exc: raise HTTPException(400,str(exc)) from None
        with connect(identity.db) as db:
            db.execute('BEGIN IMMEDIATE')
            job=db.execute('SELECT * FROM electric_qq_jobs WHERE id=? AND qq=?',(data.get('id'),qq)).fetchone()
            if not valid_job(db,job) or job['source']!='qq' or job['state']!='qq_waiting':
                raise HTTPException(409,'绑定已过期或已提交，请重新发送“绑定宿舍”。')
            if not quota_available(db,job['user_id'],time.time()): raise cooldown_error(db,job['user_id'])
            payload=cipher.encrypt(json.dumps({'provider':'wxy','selection':selection},ensure_ascii=False).encode()).decode()
            db.execute("UPDATE electric_qq_jobs SET payload=?,state='validating' WHERE id=?",(payload,job['id']))
        return {'ok':True}

    @router.post(B + '/qq/status')
    async def qq_status(request: Request):
        internal(request)
        data=await request.json()
        with connect(identity.db) as db:
            job=db.execute('SELECT * FROM electric_qq_jobs WHERE id=? AND qq=?',(data.get('id'),str(data.get('qq','')))).fetchone()
            if not valid_job(db,job) or job['source']!='qq': raise HTTPException(409,'绑定已过期或被替换，请重新发送“绑定宿舍”。')
        return {'state':job['state'],'snapshot':json.loads(job['snapshot']) if job['snapshot'] else None}

    @router.get(P + '/status')
    def status(request: Request):
        session(request)
        return {'available':False,'mode':'qq_only','can_bind':False,'binding':None,'job':None}

    @router.post(P + '/room')
    @router.post(P + '/start')
    @router.post(P + '/upload')
    @router.post(P + '/confirm')
    @router.get(P + '/assistant.zip')
    def web_binding_removed():
        raise HTTPException(410,'请直接私聊机器人发送“绑定宿舍”，无需网站账号或绑定 QQ。')

    @router.post(B + '/work')
    def work(request: Request):
        internal(request)
        with connect(identity.db) as db:
            db.execute('BEGIN IMMEDIATE')
            clean(db)
            job = db.execute("SELECT * FROM electric_qq_jobs WHERE state IN ('validating','queued','processing') AND lease_until<? ORDER BY expires LIMIT 1", (time.time(),)).fetchone()
            while job and not valid_job(db,job):
                db.execute("UPDATE electric_qq_jobs SET state='expired',payload=NULL,token_hash='' WHERE id=?",(job['id'],))
                job = db.execute("SELECT * FROM electric_qq_jobs WHERE state IN ('validating','queued','processing') AND lease_until<? ORDER BY expires LIMIT 1", (time.time(),)).fetchone()
            if not job: return {'item':None}
            if wxy_mode and json.loads(cipher.decrypt(job['payload'].encode())).get('provider')!='wxy':
                db.execute("UPDATE electric_qq_jobs SET state='expired',payload=NULL,token_hash='' WHERE id=?",(job['id'],))
                return {'item':None}
            lease = secrets.token_hex(16)
            action = 'validate' if job['state']=='validating' else 'install'
            db.execute('UPDATE electric_qq_jobs SET lease=?,lease_until=? WHERE id=?', (lease,time.time()+60,job['id']))
            return {'item':{'user_id':job['user_id'],'id':job['id'],'lease':lease,'action':action,'qq':job['qq'],'credentials':json.loads(cipher.decrypt(job['payload'].encode()))}}

    @router.post(B + '/result')
    async def result(request: Request):
        internal(request)
        data = await request.json()
        state = data.get('state')
        if state not in ('ready','done','failed'): raise HTTPException(400)
        snapshot = data.get('snapshot')
        if state != 'failed' and (not isinstance(snapshot,dict) or set(snapshot) != {'prepaid','subsidy','total','updated','room'} or any(not isinstance(v,str) or len(v)>200 for v in snapshot.values())): raise HTTPException(400)
        with connect(identity.db) as db:
            db.execute('BEGIN IMMEDIATE')
            job=db.execute('SELECT * FROM electric_qq_jobs WHERE id=? AND lease=?',(data.get('id'),data.get('lease'))).fetchone()
            if not valid_job(db,job) or job['lease_until'] <= time.time(): raise HTTPException(409)
            if (state=='ready' and job['state']!='validating') or (state=='done' and job['state'] not in ('queued','processing')): raise HTTPException(409)
            if wxy_mode and state!='failed':
                credentials=json.loads(cipher.decrypt(job['payload'].encode()))
                if credentials.get('provider')!='wxy' or snapshot['room']!=credentials['selection']['room']:
                    raise HTTPException(409,'房间校验失败')
                if state=='done':
                    if not quota_available(db,job['user_id'],time.time()): raise cooldown_error(db,job['user_id'])
                    consume_quota(db,job['user_id'],job['id'],time.time())
            encoded=json.dumps(snapshot,ensure_ascii=False) if snapshot else None
            committed_state='queued' if state=='ready' and job['source']=='qq' else state
            db.execute('UPDATE electric_qq_jobs SET state=?,snapshot=?,payload=CASE WHEN ? THEN NULL ELSE payload END,lease=NULL,lease_until=0 WHERE id=?', (committed_state,encoded,state in ('done','failed'),job['id']))
            if state=='done':
                db.execute('INSERT OR REPLACE INTO electric_qq_bindings VALUES(?,?,?,?,?,?)',(job['user_id'],job['qq'],job['id'],'active',encoded,time.time()))
        return {'ok':True}

    @router.post(B + '/bindings')
    def bindings(request: Request):
        internal(request)
        with connect(identity.db) as db:
            rows=db.execute("""SELECT b.*,COALESCE(p.alerts_enabled,1) AS alerts_enabled FROM electric_qq_bindings b
              JOIN electric_qq_accounts a ON a.id=b.user_id AND a.qq=b.qq
              LEFT JOIN electric_qq_preferences p ON p.user_id=b.user_id""").fetchall()
        return {'items':[dict(r, snapshot=json.loads(r['snapshot'])) for r in rows]}

    @router.post(B + '/health')
    async def health(request: Request):
        internal(request)
        data=await request.json()
        if data.get('state') not in ('active','auth','meter'): raise HTTPException(400)
        with connect(identity.db) as db:
            db.execute('UPDATE electric_qq_bindings SET state=? WHERE version=?',(data['state'],data.get('version')))
        return {'ok':True}
    return router
