"""Synthetic private-chat binding flow; no network and no actual messages."""
import asyncio
from astrbot_plugin_electricity.main import Electricity

class Event:
    def __init__(self,text,private=True):self.message_str=text;self.messages=[];self.stopped=False;self.private=private
    def get_sender_id(self):return '10001'
    def is_private_chat(self):return self.private
    def get_platform_id(self):return 'fixture'
    def stop_event(self):self.stopped=True
    def plain_result(self,text):return text
    async def send(self,text):self.messages.append(text)

async def main():
    plugin=Electricity.__new__(Electricity);plugin.qq_binding={};plugin.config={'platform_id':'fixture'}
    calls=[]
    async def bridge(path,data):
        calls.append(path)
        if path=='qq/start':return {'id':'fixture','expires_in':600}
        if path=='qq/room':return {'ok':True}
        if path=='qq/status':return {'state':'done','snapshot':{'room':'天佑斋 012001','total':'5.00','updated':'fixture'}}
        raise AssertionError(path)
    plugin.bridge_api=bridge
    group=Event('绑定宿舍',False);await plugin.handle(group)
    assert not group.stopped and not calls
    event=Event('绑定宿舍');await plugin.handle(event)
    assert event.stopped and '每个自然月' in event.messages[0]
    event=Event('天佑斋 012001');await plugin.handle(event)
    assert event.stopped and '已同步网站' in event.messages[-1] and not plugin.qq_binding
    event=Event('绑定宿舍');await plugin.handle(event)
    event=Event('取消绑定');await plugin.handle(event)
    assert '保持不变' in event.messages[-1] and not plugin.qq_binding
    assert calls==['qq/start','qq/room','qq/status','qq/start']

asyncio.run(main())
