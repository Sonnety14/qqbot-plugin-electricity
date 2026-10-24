"""Run in AstrBot with temporary data and an HTTP mock; no real QQ or electricity requests."""
import sys,asyncio,tempfile,json,importlib,types
from pathlib import Path
from datetime import datetime,timedelta
from unittest.mock import patch
import httpx
import importlib.util
root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('astrbot_plugin_electricity',root/'__init__.py',submodule_search_locations=[str(root)])
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
from astrbot_plugin_electricity.worker import ElectricityWorker as Electricity,CHINA
from astrbot_plugin_electricity.provider import parse_balance,SessionExpired,WrongMeter

def page(amount='19.00',room='fixture-room',updated='2026-10-23 07:00:00'):
 return f'<span id="roomaddr">{room}</span><span>预付费余额：</span><span>{amount} 元</span><span>补助电余额：</span><span>0.00 元</span><span>总余额：</span><span>{amount} 元</span><div>数据更新时间：{updated}</div>'

async def main():
 now=datetime(2026,10,23,9,tzinfo=CHINA)
 for case in ['low','equal','auth','room','stale','network','send_timeout']:
  with tempfile.TemporaryDirectory() as td:
   folder=Path(td);(folder/'credentials.json').write_text(json.dumps({'cookie':'fixture=only','room':'fixture-room'}))
   sends=[];requests=[]
   async def send(action,**kwargs):
    assert action=='send_private_msg' and kwargs['user_id']==10001
    sends.append(kwargs)
    if case=='send_timeout':raise TimeoutError()
    return {'message_id':1}
   def transport(req):
    requests.append(req);assert req.method=='GET' and str(req.url)=='http://wx.cdzht.com/meter_main'
    if case=='network':raise httpx.ConnectError('fixture')
    if case=='auth':return httpx.Response(302,headers={'Location':'https://open.weixin.qq.com/'})
    body=page('20.00' if case=='equal' else '19.00',room='other' if case=='room' else 'fixture-room',updated='2026-10-19 07:00:00' if case=='stale' else '2026-10-23 07:00:00')
    return httpx.Response(200,text=body)
   ctx=types.SimpleNamespace(get_platform_inst=lambda _:types.SimpleNamespace(bot=types.SimpleNamespace(call_action=send)))
   with patch('astrbot_plugin_electricity.worker.StarTools.get_data_dir',return_value=folder):
    obj=Electricity(ctx,{'enabled':True,'qq':'10001','threshold':20,'hour':9})
   obj.client=httpx.AsyncClient(transport=httpx.MockTransport(transport))
   await obj.check(now.replace(hour=8));assert not requests
   await obj.check(now);await obj.check(now)
   if case=='network':
    for _ in range(2):
     obj.db.execute('UPDATE checks SET attempted=0');obj.db.commit();await obj.check(now)
    assert len(requests)==3 and len(sends)==1
   else:
    assert len(requests)==1 and len(sends)==(0 if case=='equal' else 1)
   if case in ('room','auth'):assert obj.state('suspended')
   if case=='send_timeout':assert obj.db.execute('SELECT notice FROM checks').fetchone()[0]=='unknown'
   if case=='stale':assert '未及时更新' in sends[0]['message'][0]['data']['text']
   await obj.terminate()
 print('PASS daily clock, threshold, dedup, failures')
asyncio.run(main())
