import asyncio,json
from pathlib import Path
import httpx
import pytest
from astrbot_plugin_electricity.configuration import bridge_settings
from astrbot_plugin_electricity.provider import fetch_balance,parse_balance,SessionExpired,WrongMeter

ROOT=Path(__file__).resolve().parents[1]

def settings(**extra):
    return {'backend_origin':'http://127.0.0.1:8000','backend_host':'example.invalid','bridge_token':'x'*64,**extra}

def test_scoped_secret_can_come_from_environment(monkeypatch):
    monkeypatch.setenv('ELECTRICITY_BRIDGE_TOKEN','y'*64)
    origin,headers=bridge_settings(settings())
    assert origin=='http://127.0.0.1:8000' and headers['Authorization']=='Bearer '+'y'*64
    assert headers['Host']=='example.invalid'

@pytest.mark.parametrize('origin',['https://example.invalid','http://8.8.8.8','http://127.0.0.1/path','http://127.0.0.1?x=1','http://user:password@127.0.0.1','http://0.0.0.0','http://224.0.0.1'])
def test_bridge_rejects_non_private_or_ambiguous_origin(origin,monkeypatch):
    monkeypatch.delenv('ELECTRICITY_BRIDGE_TOKEN',raising=False)
    with pytest.raises(ValueError):bridge_settings(settings(backend_origin=origin))

@pytest.mark.parametrize('changes',[{'backend_host':'example.invalid\r\nX: bad'},{'backend_host':'https://example.invalid'},{'bridge_token':'short'}])
def test_bad_headers_rejected(changes,monkeypatch):
    monkeypatch.delenv('ELECTRICITY_BRIDGE_TOKEN',raising=False)
    with pytest.raises(ValueError):bridge_settings(settings(**changes))

def test_template_is_explicit_and_has_no_campus_autoload():
    schema=json.loads((ROOT/'_conf_schema.json').read_text(encoding='utf-8'))
    for key in ['platform_id','qq','backend_origin','backend_host','bridge_token','account_url']:assert schema[key]['default']==''
    assert schema['enabled']['default'] is False and schema['bridge_enabled']['default'] is False
    assert 'astrbot_plugin_campus_config' not in (ROOT/'main.py').read_text(encoding='utf-8')

def test_room_check_and_safe_parsing():
    html='<span id="roomaddr">SYNTHETIC-ROOM</span>预付费余额：10.00 元 补助电余额：2.00 元 总余额：12.00 元 数据更新时间：2026-10-23 08:00:00'
    assert parse_balance(html,'SYNTHETIC-ROOM')['total']=='12.00'
    with pytest.raises(WrongMeter):parse_balance(html,'OTHER-ROOM')
    with pytest.raises(SessionExpired):parse_balance('<html>login</html>','SYNTHETIC-ROOM')

def test_upstream_auth_redirect_is_never_followed():
    async def check():
        calls=[]
        def transport(request):
            calls.append(request)
            return httpx.Response(302,headers={'Location':'https://example.invalid/login'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            with pytest.raises(SessionExpired):await fetch_balance(client,{'cookie':'synthetic=only','room':'SYNTHETIC-ROOM'})
        assert len(calls)==1 and calls[0].method=='GET'
    asyncio.run(check())
