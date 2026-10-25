import asyncio,json,tempfile,types
from pathlib import Path
from datetime import datetime
from astrbot_plugin_electricity.main import Electricity
from astrbot_plugin_electricity.worker import ElectricityWorker,CHINA

class Event:
    def __init__(self,text):self.message_str=text;self.messages=[]
    def is_private_chat(self):return True
    def get_platform_id(self):return 'fixture'
    def get_sender_id(self):return '10001'
    def stop_event(self):pass
    def plain_result(self,text):return text
    async def send(self,text):self.messages.append(text)

async def main():
    with tempfile.TemporaryDirectory() as td:
        p=Path(td);(p/'credentials.json').write_text('{}')
        cfg={'enabled':True,'alerts_enabled':False,'platform_id':'fixture','qq':'10001','hour':9}
        worker=ElectricityWorker(None,cfg,folder=p)
        now=datetime.now(CHINA).replace(hour=12)
        queried=[];sent=[]
        async def fetch(*args):
            queried.append(1)
            return {'total':'1.00','updated':now.strftime('%Y-%m-%d %H:%M:%S')}
        async def send(text):sent.append(text)
        worker.audited_fetch=fetch;worker.send=send
        await worker.check(now)
        assert queried==[1] and not sent
        event=Event('电费查询');await worker.handle(event)
        assert '查询状态：正常' in event.messages[0] and '电费预警：已关闭' in event.messages[0]
        plugin=Electricity.__new__(Electricity)
        plugin.config=cfg;plugin.qq_binding={};plugin.workers={'1':worker}
        async def bridge(path,data=None):
            assert path=='qq/alerts'
            return {'alerts_enabled':not worker.config['alerts_enabled']}
        plugin.bridge_api=bridge
        for enabled in (True,False):
            event=Event('电费预警');await plugin.handle(event)
            assert worker.config['alerts_enabled']==enabled
            assert ('已开启' if enabled else '已关闭') in event.messages[0]
        await worker.terminate()
asyncio.run(main())
