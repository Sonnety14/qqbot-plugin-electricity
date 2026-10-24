"""Bridge is opt-in and restricted to a private IP and explicit virtual host."""
import os
import ipaddress
import re
from urllib.parse import urlsplit


def bridge_settings(config):
    origin=config.get('backend_origin','')
    u=urlsplit(origin)
    try:ip=ipaddress.ip_address(u.hostname or '')
    except ValueError:raise ValueError('Private backend IP required') from None
    if (u.scheme!='http' or not (ip.is_private or ip.is_loopback) or ip.is_unspecified
        or ip.is_multicast or u.username or u.password or u.path or u.query or u.fragment):
        raise ValueError('Private backend origin required')
    host=config.get('backend_host','')
    token=os.environ.get('ELECTRICITY_BRIDGE_TOKEN','').strip() or config.get('bridge_token','')
    if not re.fullmatch(r'[A-Za-z0-9.-]+(?::[0-9]{1,5})?',host):raise ValueError('Explicit Host required')
    if not re.fullmatch(r'[A-Za-z0-9_-]{40,128}',token):raise ValueError('Scoped bridge token required')
    return origin,{'Host':host,'Authorization':'Bearer '+token}
