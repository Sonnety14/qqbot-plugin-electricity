"""Normal SWJTU CAS login. Never solve challenges or log upstream responses."""
import base64
import secrets
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .provider import SessionExpired
from .wxy_provider import ORIGIN, CATEGORY, api

CAS = 'https://cas.swjtu.edu.cn'
LOGIN = CAS + '/authserver/login?' + urlencode({'service': ORIGIN + '/wechat/cas/login.html'})


class AuthInteractionRequired(SessionExpired):
    """Do not retry passwords until the operator has resolved the challenge."""


class PasswordForm(HTMLParser):
    def __init__(self, html, form_id='pwdFromId'):
        super().__init__()
        self.form_id = form_id
        self.active = False
        self.fields = {}
        self.salt = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form':
            self.active = attrs.get('id') == self.form_id
        if tag == 'input' and self.active:
            if (attrs.get('type') == 'hidden' or self.form_id == 'postForm') and attrs.get('name'):
                self.fields[attrs['name']] = attrs.get('value', '')
            if attrs.get('id') == 'pwdEncryptSalt':
                self.salt = attrs.get('value')

    def handle_endtag(self, tag):
        if tag == 'form':
            self.active = False


def encrypt_password(password, salt):
    alphabet = 'ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678'
    random_text = lambda n: ''.join(secrets.choice(alphabet) for _ in range(n))
    padder = padding.PKCS7(128).padder()
    raw = (random_text(64) + password).encode('utf-8')
    padded = padder.update(raw) + padder.finalize()
    encryptor = Cipher(algorithms.AES(salt.strip().encode()), modes.CBC(random_text(16).encode())).encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode()


def allowed(url):
    part = urlsplit(str(url))
    if part.scheme != 'https' or part.hostname not in ('cas.swjtu.edu.cn', 'wxy.swjtu.edu.cn') or part.port not in (None, 443) or part.username or part.password:
        raise AuthInteractionRequired('Unexpected authentication destination')


async def follow(client, response):
    for _ in range(12):
        if response.status_code not in (301, 302, 303, 307, 308):
            response.raise_for_status()
            return response
        url = urljoin(str(response.url), response.headers.get('location', ''))
        allowed(url)
        # A password POST must never be replayed at a redirected destination.
        if response.status_code in (307, 308) and response.request.method != 'GET':
            raise AuthInteractionRequired('Unexpected authentication redirect')
        response = await client.get(url, follow_redirects=False)
    raise AuthInteractionRequired('Authentication redirect limit')


def wxy_cookie(client):
    found = [c.value for c in client.cookies.jar
             if c.name == 'JSESSIONID' and c.domain.lstrip('.') == 'wxy.swjtu.edu.cn']
    return 'JSESSIONID=' + found[-1] if found else ''


async def login(client, credentials):
    """Try existing CAS cookies first, then at most one password submission."""
    response = await follow(client, await client.get(LOGIN, follow_redirects=False))
    if urlsplit(str(response.url)).hostname == 'cas.swjtu.edu.cn':
        form = PasswordForm(response.text)
        if not form.salt or not form.fields.get('execution'):
            raise AuthInteractionRequired('Login page requires manual authentication')
        check = await client.get(CAS + '/authserver/checkNeedCaptcha.htl',
                                 params={'username': credentials['username']}, follow_redirects=False)
        check.raise_for_status()
        try:
            challenge = check.json()['isNeed']
        except (ValueError, KeyError, TypeError):
            raise AuthInteractionRequired('Cannot verify challenge requirement') from None
        if challenge is not False:
            raise AuthInteractionRequired('School requires verification code')
        fields = {**form.fields, 'username': credentials['username'],
                  'password': encrypt_password(credentials['password'], form.salt),
                  'rememberMe': 'true', 'captcha': ''}
        response = await follow(client, await client.post(LOGIN, data=fields,
            headers={'Origin': CAS, 'Referer': LOGIN}, follow_redirects=False))
        if urlsplit(str(response.url)).hostname == 'cas.swjtu.edu.cn':
            raise AuthInteractionRequired('Login rejected or additional verification required')
    # CAS callback renders an auto-submitted identity form; HTTP redirects alone
    # do not establish the campus card session. Only submit to this fixed target.
    if urlsplit(str(response.url)).hostname == 'wxy.swjtu.edu.cn':
        bridge = PasswordForm(response.text, 'postForm')
        if bridge.fields:
            if set(bridge.fields) != {'json', 'token', 'xxbh', 'jkbh'} or bridge.fields['jkbh'] != '0024' or "'/cardbag/card.html'" not in response.text:
                raise AuthInteractionRequired('Unexpected campus identity bridge')
            await follow(client, await client.post(ORIGIN + '/wechat/cardbag/card.html',
                data=bridge.fields, headers={'Origin': ORIGIN, 'Referer': str(response.url)}, follow_redirects=False))
    cookie = wxy_cookie(client)
    # A redirected landing page is not proof of successful authentication.
    await api(client, cookie, 'queryElecArea', aid=CATEGORY)
    return cookie
