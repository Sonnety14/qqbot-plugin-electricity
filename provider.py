"""Read-only balance retrieval. No payment or room-selection requests."""
from decimal import Decimal, InvalidOperation
from html import unescape
from html.parser import HTMLParser
import re

URL = 'http://wx.cdzht.com/meter_main'

class SessionExpired(Exception): pass
class WrongMeter(Exception): pass

class Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]; self.skip=0
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style'): self.skip+=1
    def handle_endtag(self, tag):
        if tag in ('script','style'): self.skip=max(0,self.skip-1)
    def handle_data(self, data):
        if not self.skip:self.parts.append(data)

def parse_balance(html, expected_room):
    room=re.search(r'<span\b[^>]*\bid=[\"\x27]roomaddr[\"\x27][^>]*>(.*?)</span>',html,re.S|re.I)
    if not room: raise SessionExpired('missing balance page')
    room=unescape(re.sub(r'<[^>]+>','',room.group(1))).strip()
    if room != expected_room: raise WrongMeter('selected meter changed')
    parser=Text();parser.feed(html);text=' '.join(parser.parts)
    values={}
    for name,label in [('prepaid','预付费余额'),('subsidy','补助电余额'),('total','总余额')]:
        found=re.search(re.escape(label)+r'\s*[：:]\s*(-?\d+(?:\.\d{1,2})?)\s*元',text)
        if not found:raise ValueError('missing amount')
        values[name]=str(Decimal(found.group(1)).quantize(Decimal('0.01')))
    updated=re.search(r'数据更新时间\s*[：:]\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})',text)
    if not updated:raise ValueError('missing reading timestamp')
    return {**values,'updated':updated.group(1),'room':room}

async def fetch_balance(client, credentials):
    # Deliberately never follow an OAuth redirect or perform a POST.
    async with client.stream('GET',URL,headers={'Cookie':credentials['cookie'],
            'User-Agent':credentials.get('user_agent','Mozilla/5.0'),
            'Cache-Control':'no-cache'},follow_redirects=False) as r:
        if r.status_code in (301,302,303,307,308,401,403):raise SessionExpired('authorization required')
        r.raise_for_status()
        raw=bytearray()
        async for chunk in r.aiter_bytes():
            raw.extend(chunk)
            if len(raw)>256000:raise ValueError('response too large')
    return parse_balance(raw.decode('utf-8'),credentials['room'])
