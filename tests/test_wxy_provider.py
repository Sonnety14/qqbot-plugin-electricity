import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from astrbot_plugin_electricity.wxy_provider import fetch_wxy_balance, parse_result, select_unique, api
from astrbot_plugin_electricity.provider import WrongMeter, SessionExpired


class WxyTests(unittest.TestCase):
    def test_balance_identity(self):
        args = ({'building': '犀浦校区天佑斋1栋'}, {'floor': '犀浦校区天佑斋1栋2楼'}, {'room': '2001'}, '天佑斋 012001')
        result = parse_result({'errmsg': '房间号[犀浦校区天佑斋1栋2楼2001]当前电量10.00当前余额5.00'}, *args)
        self.assertEqual(result['total'], '5.00')
        with self.assertRaises(WrongMeter):
            parse_result({'errmsg': '房间号[犀浦校区天佑斋1栋2楼3001]当前电量64.77当前余额34.74'}, *args)

    def test_hongzhe_label_observed(self):
        result = parse_result({'errmsg': '房间号[犀浦校区鸿哲斋2号楼1楼H020101]当前电量16.00当前余额8.00'},
            {'building':'犀浦校区鸿哲斋2号楼'}, {'floor':'犀浦校区鸿哲斋2号楼1楼'}, {'room':'1001'}, '鸿哲斋 021001')
        self.assertEqual(result['total'], '8.00')

    def test_selection_is_not_positional(self):
        self.assertEqual(select_unique([{'name':'other'}, {'name':'target'}], 'name', 'target'), {'name':'target'})
        for rows in ([], [{'name':'target'}, {'name':'target'}]):
            with self.assertRaises(WrongMeter):
                select_unique(rows, 'name', 'target')

    def test_no_transaction_endpoint(self):
        with self.assertRaises(ValueError):
            asyncio.run(api(None, 'fixture', 'elecZhtOrder'))

    def test_expired_redirect(self):
        class Response:
            status_code = 302
        class Client:
            async def post(self, *args, **kwargs):
                return Response()
        with self.assertRaises(SessionExpired):
            asyncio.run(api(Client(), 'fixture', 'queryElecArea', aid='fixture'))

    def test_query_carries_explicit_hierarchy(self):
        calls = []
        class Response:
            status_code = 200
            content = b'fixture'
            def __init__(self, data): self.data = data
            def raise_for_status(self): pass
            def json(self): return self.data
        class Client:
            async def post(self, url, **kwargs):
                action = url.rsplit('/', 1)[-1][:-5]
                calls.append((action, kwargs['data']))
                responses = {
                    'queryElecArea': {'areatab':[{'area':'other','areaname':'other'},{'area':'chosen','areaname':'犀浦校区'}]},
                    'queryElecBuilding': {'buildingtab':[{'buildingid':'chosen','building':'犀浦校区天佑斋1栋'}]},
                    'queryElecFloor': {'floortab':[{'floorid':'chosen','floor':'犀浦校区天佑斋1栋2楼'}]},
                    'queryElecRoom': {'roomtab':[{'roomid':'wrong','room':'3001'},{'roomid':'chosen','room':'2001'}]},
                    'queryElecRoomInfo': {'errmsg':'房间号[犀浦校区天佑斋1栋2楼2001]当前电量10.00当前余额5.00'},
                }
                return Response({'retcode':0, **responses[action]})
        result = asyncio.run(fetch_wxy_balance(Client(), 'fixture', {
            'area':'天佑斋','building':1,'floor':2,'room_code':'2001','room':'天佑斋 012001'}))
        self.assertEqual(result['total'], '5.00')
        self.assertEqual(len(calls), 5)
        final = calls[-1][1]
        self.assertEqual(json.loads(final['room'])['roomid'], 'chosen')
        self.assertEqual(json.loads(final['area'])['area'], 'chosen')
        self.assertEqual(final['aid'], '0030000000009801')


if __name__ == '__main__': unittest.main()
