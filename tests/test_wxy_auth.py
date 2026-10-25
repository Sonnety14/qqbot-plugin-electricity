import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock

import httpx
from cryptography.fernet import Fernet
from astrbot_plugin_electricity.wxy_auth import login, AuthInteractionRequired, allowed
from astrbot_plugin_electricity.wxy_session import WxySession
from astrbot_plugin_electricity.provider import SessionExpired, WrongMeter
from astrbot_plugin_electricity.wxy_provider import api

FORM = '''<form id="pwdFromId"><input type="hidden" id="pwdEncryptSalt" value="0123456789abcdef">
<input type="hidden" name="execution" value="e1s1"><input type="hidden" name="cllt" value="userNameLogin"></form>'''
BRIDGE = '''<form id="postForm"><input name="json" value="fixture"><input name="token" value="fixture">
<input name="xxbh" value="fixture"><input name="jkbh" value="0024"></form><script>const url = '/cardbag/card.html';</script>'''


class AuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_school_expiry_code_and_other_failure(self):
        for code, error in [('91001',SessionExpired),('500',ValueError)]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'retcode':code}))) as client:
                with self.assertRaises(error): await api(client,'JSESSIONID=fixture','queryElecArea')

    async def test_complete_flow_and_challenge(self):
        for challenge in (False, True):
            calls = []
            def handler(request):
                path = request.url.path
                calls.append((request.method, path))
                if path.endswith('checkNeedCaptcha.htl'):
                    return httpx.Response(200, json={'isNeed':challenge})
                if path == '/authserver/login':
                    if request.method == 'GET':
                        return httpx.Response(200, text=FORM)
                    self.assertNotIn(b'fixture-password', request.content)
                    return httpx.Response(302, headers={'location':'https://wxy.swjtu.edu.cn/wechat/cas/login.html?ticket=fixture'})
                if path == '/wechat/cas/login.html':
                    return httpx.Response(200, text=BRIDGE, headers={'set-cookie':'JSESSIONID=fixture; Path=/wechat'})
                if path == '/wechat/cardbag/card.html':
                    return httpx.Response(200, text='ready')
                if path.endswith('queryElecArea.html'):
                    self.assertIn(('POST','/wechat/cardbag/card.html'), calls)
                    return httpx.Response(200, json={'retcode':0,'areatab':[]})
                raise AssertionError(path)
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                if challenge:
                    with self.assertRaises(AuthInteractionRequired):
                        await login(client, {'username':'fixture','password':'fixture-password'})
                    self.assertFalse(any(method=='POST' for method,path in calls))
                else:
                    self.assertEqual(await login(client, {'username':'fixture','password':'fixture-password'}), 'JSESSIONID=fixture')

    async def test_block_and_backoff_survive_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            cipher=Fernet(Fernet.generate_key())
            session=WxySession(Path(folder),cipher)
            session.save({'cookie':'JSESSIONID=old'})
            session.save_encrypted(session.auth_path,{'credentials':{'username':'fixture','password':'fixture'}})
            with patch('astrbot_plugin_electricity.wxy_session.login',AsyncMock(side_effect=AuthInteractionRequired('captcha'))) as attempt:
                with self.assertRaises(AuthInteractionRequired): await session.renew()
                await session.close()
                session=WxySession(Path(folder),cipher)
                with self.assertRaises(AuthInteractionRequired): await session.renew()
                self.assertEqual(attempt.await_count,1)
                self.assertEqual(session.read()['cookie'],'JSESSIONID=old')
            session.save_encrypted(session.auth_path,{'credentials':{},'retry_after':9999999999})
            with patch('astrbot_plugin_electricity.wxy_session.login',AsyncMock()) as attempt:
                with self.assertRaises(SessionExpired): await session.renew()
                attempt.assert_not_called()
            await session.close()

    async def test_concurrent_expiry_one_renewal_and_wrong_room_preserves_session(self):
        with tempfile.TemporaryDirectory() as folder:
            session=WxySession(Path(folder),Fernet(Fernet.generate_key()))
            session.save({'cookie':'JSESSIONID=old'})
            session.save_encrypted(session.auth_path,{'credentials':{}})
            async def fetch(cookie,selection):
                if cookie.endswith('old'): raise SessionExpired('expired')
                return {'retcode':0}
            session.fetch=fetch
            with patch('astrbot_plugin_electricity.wxy_session.login',AsyncMock(return_value='JSESSIONID=new')) as attempt:
                await asyncio.gather(session.query(),session.query())
                self.assertEqual(attempt.await_count,1)
            session.save_encrypted(session.auth_path,{'credentials':{}})
            with (patch('astrbot_plugin_electricity.wxy_session.login',AsyncMock(return_value='JSESSIONID=unverified')),
                 patch('astrbot_plugin_electricity.wxy_session.fetch_wxy_balance',AsyncMock(side_effect=WrongMeter('wrong')))):
                with self.assertRaises(SessionExpired): await session.renew({'room':'fixture'})
                self.assertEqual(session.read()['cookie'],'JSESSIONID=new')
            await session.close()

    def test_redirect_allowlist(self):
        for url in ('http://cas.swjtu.edu.cn','https://cas.swjtu.edu.cn.evil.test','https://evil.test','https://user@cas.swjtu.edu.cn','https://cas.swjtu.edu.cn:444/'):
            with self.assertRaises(AuthInteractionRequired): allowed(url)


if __name__=='__main__': unittest.main()
