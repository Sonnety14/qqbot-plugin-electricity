"""Exercise plugin logic offline with a minimal AstrBot API stub, not a live bot."""
import logging
from pathlib import Path
import runpy
import sys
import types


def test_plugin_scheduler_and_renewal_offline(monkeypatch):
    api=types.ModuleType('astrbot.api')
    api.logger=logging.getLogger('electricity-test')
    api.AstrBotConfig=dict
    stars=types.ModuleType('astrbot.api.star')
    class Star:
        def __init__(self,context):self.context=context
    stars.Star=Star
    stars.Context=object
    stars.StarTools=types.SimpleNamespace(get_data_dir=lambda name:None)
    stars.register=lambda *args:lambda cls:cls
    event=types.ModuleType('astrbot.api.event')
    event.filter=types.SimpleNamespace(EventMessageType=types.SimpleNamespace(ALL='all'),event_message_type=lambda *args,**kwargs:lambda method:method)
    for name,module in [('astrbot',types.ModuleType('astrbot')),('astrbot.api',api),('astrbot.api.star',stars),('astrbot.api.event',event)]:
        monkeypatch.setitem(sys.modules,name,module)
    root=Path(__file__).resolve().parents[1]
    runpy.run_path(str(root/'tests/check_astrbot_runtime.py'),run_name='__main__')
    runpy.run_path(str(root/'tests/check_multi_runtime.py'),run_name='__main__')
    runpy.run_path(str(root/'tests/check_qq_binding.py'),run_name='__main__')

    runpy.run_path(str(root/'tests/check_alerts.py'),run_name='__main__')
