"""Shared encrypted WXY session with serialized queries and durable health audit."""
import asyncio
import hashlib
import json
import os
import sqlite3
import time

import httpx

from .wxy_provider import api, fetch_wxy_balance, tls_context, CATEGORY
from .provider import SessionExpired
from .wxy_auth import login, AuthInteractionRequired


class WxySession:
    def __init__(self, folder, cipher):
        self.path = folder / 'wxy-session.enc'
        self.cipher = cipher
        self.auth_path = folder / 'wxy-auth.enc'
        self.lock = asyncio.Lock()
        self.client = httpx.AsyncClient(timeout=25, trust_env=False, verify=tls_context())
        self.db = sqlite3.connect(folder / 'wxy-health.sqlite3')
        self.db.execute('''CREATE TABLE IF NOT EXISTS queries (
            id INTEGER PRIMARY KEY, started REAL, finished REAL, kind TEXT,
            session_version TEXT, outcome TEXT, error_type TEXT)''')

    def read(self):
        return json.loads(self.cipher.decrypt(self.path.read_bytes()))

    def save(self, session):
        self.save_encrypted(self.path, session)

    def save_encrypted(self, path, session):
        temp = path.with_suffix('.pending')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(self.cipher.encrypt(json.dumps(session).encode()))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        os.chmod(path, 0o600)

    async def renew(self, selection=None):
        if not self.auth_path.exists():
            raise SessionExpired('No automatic login configured')
        state = json.loads(self.cipher.decrypt(self.auth_path.read_bytes()))
        if state.get('blocked'):
            raise AuthInteractionRequired('Automatic login paused; operator verification required')
        if state.get('retry_after', 0) > time.time():
            raise SessionExpired('Automatic login waiting for retry')
        # Persist before attempting: a process restart must not bypass rate limits.
        state['retry_after'] = time.time() + 3600
        self.save_encrypted(self.auth_path, state)
        started = time.time()
        outcome, error = 'failed', None
        try:
            async with httpx.AsyncClient(timeout=30, trust_env=False, verify=tls_context()) as client:
                for item in state.get('cas_cookies', []):
                    if item.get('domain', '').lstrip('.') == 'cas.swjtu.edu.cn':
                        client.cookies.set(**item)
                cookie = await login(client, state['credentials'])
                if selection:
                    await fetch_wxy_balance(client, cookie, selection)
                state['cas_cookies'] = [dict(name=c.name, value=c.value, domain=c.domain, path=c.path)
                    for c in client.cookies.jar if c.domain.lstrip('.') == 'cas.swjtu.edu.cn']
                state['last_success'] = time.time()
                state['blocked'] = False
                self.save_encrypted(self.auth_path, state)
                session = self.read() if self.path.exists() else {}
                session['cookie'] = cookie
                self.save(session)
                outcome = 'ok'
                return cookie
        except AuthInteractionRequired as exc:
            state['blocked'] = True
            state['blocked_reason'] = str(exc)
            self.save_encrypted(self.auth_path, state)
            error = type(exc).__name__
            raise
        except Exception as exc:
            error = type(exc).__name__
            raise SessionExpired('Automatic login failed; retry delayed') from None
        finally:
            with self.db:
                self.db.execute('INSERT INTO queries(started,finished,kind,outcome,error_type) VALUES (?,?,?,?,?)',
                    (started,time.time(),'login',outcome,error))

    async def fetch(self, cookie, selection):
        self.client.cookies.clear()
        if selection:
            return await fetch_wxy_balance(self.client, cookie, selection)
        return await api(self.client, cookie, 'queryElecArea', aid=CATEGORY)

    async def query(self, selection=None):
        async with self.lock:
            session = self.read()
            cookie = session['cookie']
            version = hashlib.sha256(cookie.encode()).hexdigest()[:16]
            with self.db:
                ident = self.db.execute('INSERT INTO queries(started,kind,session_version,outcome) VALUES (?,?,?,?)',
                    (time.time(), 'balance' if selection else 'keepalive', version, 'running')).lastrowid
            outcome, error = 'cancelled', None
            try:
                # Never carry cookies from another account/request in the jar.
                try:
                    result = await self.fetch(cookie, selection)
                except SessionExpired:
                    cookie = await self.renew(selection)
                    session = self.read()
                    result = await self.fetch(cookie, selection)
                # Persist server-issued session rotation, only after a successful query.
                rotated = [c.value for c in self.client.cookies.jar
                           if c.name == 'JSESSIONID' and c.domain.lstrip('.') == 'wxy.swjtu.edu.cn']
                if rotated:
                    session['cookie'] = 'JSESSIONID=' + rotated[-1]
                    self.save(session)
                outcome = 'ok'
                return result
            except Exception as exc:
                outcome, error = 'failed', type(exc).__name__
                raise
            finally:
                with self.db:
                    self.db.execute('UPDATE queries SET finished=?,outcome=?,error_type=? WHERE id=?',
                                    (time.time(), outcome, error, ident))

    async def close(self):
        await self.client.aclose()
        self.db.close()
