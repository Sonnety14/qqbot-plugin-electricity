"""Read-only WXY electricity queries; never call transaction/payment endpoints."""
import json
import re
import ssl
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .provider import SessionExpired, WrongMeter

ORIGIN = 'https://wxy.swjtu.edu.cn'
CATEGORY = '0030000000009801'
CHINA = timezone(timedelta(hours=8))
ENDPOINTS = frozenset(('queryApplist', 'queryElecArea', 'queryElecBuilding',
                       'queryElecFloor', 'queryElecRoom', 'queryElecRoomInfo'))


def tls_context():
    # The school's server negotiates TLS 1.2 / AES256-SHA (also verified with
    # system curl). Keep CA/hostname verification and TLS >= 1.2 enabled.
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers('DEFAULT:AES256-SHA')
    return context


def select_unique(items, field, expected):
    if not isinstance(items, list):
        raise ValueError('Invalid WXY options')
    found = [item for item in items if isinstance(item, dict) and item.get(field) == expected]
    if len(found) != 1:
        raise WrongMeter('Room selection unavailable or ambiguous')
    return found[0]


def parse_result(data, building, floor, room, display_room):
    message = data.get('errmsg', '')
    if not isinstance(message, str):
        raise ValueError('Invalid WXY balance')
    # The response includes the room label. Reject previous/default-room results.
    match = re.fullmatch(r'房间号\[(.+)\]当前电量(-?\d+(?:\.\d+)?)当前余额(-?\d+(?:\.\d+)?)', message.strip())
    if not match:
        raise ValueError('Unrecognized WXY balance response')
    room_label = str(room['room'])
    alternatives = {str(floor['floor']) + room_label}
    # Hongzhe responses use H + building(2) + floor(2) + room(2),
    # while the selector labels use floor + room(3).
    hongzhe = re.fullmatch(r'犀浦校区鸿哲斋([23])号楼', str(building['building']))
    floor_match = re.fullmatch(re.escape(str(building['building'])) + r'(\d+)楼', str(floor['floor']))
    if hongzhe and floor_match and room_label.isascii() and room_label.isdigit():
        floor_number = int(floor_match[1])
        room_number = int(room_label) - floor_number * 1000
        if 1 <= room_number <= 99:
            alternatives.add(str(floor['floor']) + f'H{int(hongzhe[1]):02d}{floor_number:02d}{room_number:02d}')
    if match[1] not in alternatives:
        raise WrongMeter('WXY returned a different room')
    balance = format(Decimal(match[3]).quantize(Decimal('.01')), 'f')
    # WXY does not expose a meter update timestamp: this is query receipt time.
    return {'prepaid': balance, 'subsidy': '0.00', 'total': balance,
            'updated': datetime.now(CHINA).strftime('%Y-%m-%d %H:%M:%S'), 'room': display_room}


async def api(client, cookie, action, **params):
    if action not in ENDPOINTS:
        raise ValueError('Not a permitted query endpoint')
    if not isinstance(cookie, str) or not cookie or '\r' in cookie or '\n' in cookie:
        raise SessionExpired('Missing WXY session')
    response = await client.post(ORIGIN + '/wechat/basicQuery/' + action + '.html',
        data={k: json.dumps(v, ensure_ascii=False, separators=(',', ':')) if isinstance(v, dict) else v
              for k, v in params.items()},
        headers={'Cookie': cookie, 'Origin': ORIGIN, 'Referer': ORIGIN + '/wechat/elecpay/queryelec.html',
                 'X-Requested-With': 'XMLHttpRequest'}, follow_redirects=False)
    if response.status_code in (301, 302, 303, 307, 308, 401, 403):
        raise SessionExpired('WXY authentication required')
    response.raise_for_status()
    if len(response.content) > 1024 * 1024:
        raise ValueError('WXY response too large')
    try:
        data = response.json()
    except ValueError:
        raise SessionExpired('WXY authentication response') from None
    if isinstance(data, dict) and str(data.get('retcode')) == '91001':
        raise SessionExpired('WXY session timed out')
    if not isinstance(data, dict) or str(data.get('retcode')) != '0':
        # Do not print upstream errors: they can contain account identifiers.
        raise ValueError('WXY query unsuccessful')
    return data


async def fetch_wxy_balance(client, cookie, selection):
    building_label = ('犀浦校区天佑斋' + str(selection['building']) + '栋'
                      if selection['area'] == '天佑斋'
                      else '犀浦校区鸿哲斋' + str(selection['building']) + '号楼')
    base = {'aid': CATEGORY}
    areas = await api(client, cookie, 'queryElecArea', **base)
    base['area'] = select_unique(areas.get('areatab'), 'areaname', '犀浦校区')
    buildings = await api(client, cookie, 'queryElecBuilding', **base)
    base['building'] = select_unique(buildings.get('buildingtab'), 'building', building_label)
    floors = await api(client, cookie, 'queryElecFloor', **base)
    base['floor'] = select_unique(floors.get('floortab'), 'floor', building_label + str(selection['floor']) + '楼')
    rooms = await api(client, cookie, 'queryElecRoom', **base)
    base['room'] = select_unique(rooms.get('roomtab'), 'room', selection['room_code'])
    result = await api(client, cookie, 'queryElecRoomInfo', **base)
    return parse_result(result, base['building'], base['floor'], base['room'], selection['room'])
