"""Run with the plugin stopped. Credentials are entered locally, never CLI args."""
import argparse
import asyncio
import getpass
import os
from pathlib import Path
import sys
import types
from cryptography.fernet import Fernet

root=Path(__file__).resolve().parents[1]
package=types.ModuleType('electricity_setup');package.__path__=[str(root)]
sys.modules['electricity_setup']=package
from electricity_setup.wxy_session import WxySession

async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,required=True,help='Actual AstrBot plugin_data/astrbot_plugin_electricity directory')
    args=parser.parse_args()
    folder=args.data_dir.resolve();folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    key=folder/'accounts.key'
    if not key.exists():
        fd=os.open(key,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as stream:stream.write(Fernet.generate_key())
    cipher=Fernet(key.read_bytes())
    username=input('School CAS username: ').strip()
    password=getpass.getpass('School CAS password (hidden): ')
    if not username or not password:raise SystemExit('Credentials must not be empty')
    session=WxySession(folder,cipher)
    original=session.auth_path.read_bytes() if session.auth_path.exists() else None
    try:
        session.save_encrypted(session.auth_path,{'credentials':{'username':username,'password':password},'cas_cookies':[]})
        await session.renew()
        print('Login and electricity API verified. Encrypted configuration saved. Restart the plugin.')
    except Exception as exc:
        if original is None:session.auth_path.unlink(missing_ok=True)
        else:
            # Restore previous encrypted configuration without exposing its contents.
            session.save_encrypted(session.auth_path,__import__('json').loads(cipher.decrypt(original)))
        print('Setup failed: '+type(exc).__name__+'. Previous configuration preserved; resolve school verification before retrying.')
        raise SystemExit(1) from None
    finally:await session.close()

if __name__=='__main__':asyncio.run(main())
