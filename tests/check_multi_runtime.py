"""Two-account migration, renewal, failure and recipient-isolation checks; all I/O mocked."""
import asyncio,json,tempfile,types,sqlite3
from pathlib import Path
from datetime import datetime,timedelta
from unittest.mock import patch
import sys,importlib.util
root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('astrbot_plugin_electricity',root/'__init__.py',submodule_search_locations=[str(root)])
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
from astrbot_plugin_electricity.main import Electricity,CHINA
from astrbot_plugin_electricity.provider import SessionExpired

async def exercise():
 with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
  folder=Path(td); now=datetime.now(CHINA).replace(hour=0,minute=0,second=0,microsecond=0)
  snap=lambda room:{'room':room,'total':'10.00','prepaid':'10.00','subsidy':'0.00','updated':now.strftime('%Y-%m-%d %H:%M:%S')}
  a={'user_id':1,'qq':'10001','version':'a'*32,'snapshot':snap('A')}
  b={'user_id':2,'qq':'10002','version':'b'*32,'snapshot':snap('B')}
  legacy={'cookie':'connect.sid=account-A-secret','room':'A','binding_version':a['version']}
  original=json.dumps(legacy).encode();(folder/'credentials.json').write_bytes(original)
  with sqlite3.connect(folder/'checks.sqlite3') as db:
   db.executescript('CREATE TABLE state(key TEXT PRIMARY KEY,value TEXT NOT NULL); CREATE TABLE checks(day TEXT PRIMARY KEY,attempts INTEGER NOT NULL,attempted REAL NOT NULL,status TEXT NOT NULL,snapshot TEXT,notice TEXT);')
   db.execute('INSERT INTO checks VALUES (?,1,0,?,?,?)',((now-timedelta(days=1)).date().isoformat(),'ok',json.dumps(snap('A')),'sent'))
  sends=[];requests=[];bindings=[a];job=None;reject=False
  async def send(action,**kw):sends.append(kw);return {'message_id':1}
  ctx=types.SimpleNamespace(get_platform_inst=lambda _:types.SimpleNamespace(bot=types.SimpleNamespace(call_action=send)))
  with patch('astrbot_plugin_electricity.main.StarTools.get_data_dir',return_value=folder):
   obj=Electricity(ctx,{'enabled':True,'qq':a['qq'],'platform_id':'test','threshold':20,'hour':9})
  async def api(path,data=None):
   nonlocal job
   if path=='work':return {'item':job}
   if path=='bindings':return {'items':bindings}
   if path=='result':
    if reject:raise RuntimeError('Lost acknowledgement')
    if data['state']=='done':
     new={**job,'version':job['id'],'snapshot':snap(job['credentials']['room'])}
     bindings[:]=[x for x in bindings if x['user_id']!=new['user_id']]+[new]
    job=None
   return {'ok':True}
  async def fetch(client,credential):
   requests.append(credential.copy())
   if credential['cookie']=='expired': raise SessionExpired()
   return snap(credential['room'])
  obj.bridge_api=api
  with patch('astrbot_plugin_electricity.main.fetch_balance',fetch),patch('astrbot_plugin_electricity.worker.fetch_balance',fetch),patch('astrbot_plugin_electricity.session_audit.fetch_balance',fetch):
   await obj.binding_tick()
   assert obj.workers['1'].db.execute('SELECT notice FROM checks').fetchone()[0]=='sent'
   assert (folder/'credentials.json').read_bytes()==original
   assert b'account-A-secret' not in (folder/'accounts/1'/('a'*32+'.enc')).read_bytes()
   job={**b,'id':b['version'],'lease':'test','action':'install','credentials':{'cookie':'connect.sid=account-B-secret','room':'B'}}
   await obj.binding_tick(); assert set(obj.workers)=={'1','2'}
   # Both accounts have separate records; one expired account does not stop the other.
   for w in obj.workers.values(): w.db.execute('DELETE FROM checks'); w.db.commit()
   await obj.check(now.replace(hour=9))
   assert {x['user_id'] for x in sends}=={10001,10002}, (sends, [(uid, [dict(r) for r in w.db.execute('SELECT * FROM checks')]) for uid,w in obj.workers.items()])
   assert {x['room'] for x in requests}=={'A','B'}
   await obj.check(now.replace(hour=9));assert len(sends)==2
   # Lost install acknowledgement preserves the existing active worker/version.
   job={'user_id':2,'qq':b['qq'],'id':'c'*32,'lease':'test','action':'install','credentials':{'cookie':'renewed-B','room':'B'}}
   reject=True
   try:await obj.binding_tick()
   except RuntimeError:pass
   assert obj.workers['2'].version=='b'*32 and obj.workers['1'].version=='a'*32
   reject=False;await obj.binding_tick()
   assert obj.workers['2'].version=='c'*32 and obj.workers['2'].db.execute('SELECT notice FROM checks').fetchone()[0]=='sent'
   bad=obj.workers['2']
   bad.credential_path.write_bytes(obj.cipher.encrypt(json.dumps({'cookie':'expired','room':'B'}).encode()))
   await obj.check((now+timedelta(days=1)).replace(hour=9))
   assert bad.state('suspended')=='auth'
   assert obj.workers['1'].db.execute('SELECT status FROM checks ORDER BY day DESC LIMIT 1').fetchone()[0]=='ok'

   assert not obj.workers['1'].state('suspended')
   bindings[:]=[x for x in bindings if x['user_id']==1]
   await obj.binding_tick();assert set(obj.workers)=={'1'}
   assert (folder/'credentials.json').read_bytes()==original
  await obj.terminate()
 print('PASS multi-user migration, credential encryption, independent notices, renewal acknowledgements, revocation')

asyncio.run(exercise())
