"""Account-local session lifetime experiment; stores no credentials or room names."""
import asyncio
import time
from datetime import datetime, timedelta, timezone

from .provider import fetch_balance, SessionExpired, WrongMeter


class SessionAudit:
    def init_session_audit(self):
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS session_queries(
                id INTEGER PRIMARY KEY, version TEXT NOT NULL, kind TEXT NOT NULL,
                started REAL NOT NULL, finished REAL, outcome TEXT NOT NULL,
                reading_time TEXT, error_type TEXT);
            CREATE TABLE IF NOT EXISTS session_lifetimes(
                version TEXT PRIMARY KEY, credential_saved REAL NOT NULL,
                observed_since REAL NOT NULL, last_success REAL,
                first_auth_failure REAL, first_meter_failure REAL);
        ''')

    def audit_version(self):
        return getattr(self, 'version', '') or self.state('credential_hash') or 'legacy'

    async def audited_fetch(self, credentials, kind):
        allowed = {str(v) for v in self.config.get('keepalive_account_ids', [])}
        if str(getattr(self, 'uid', '')) not in allowed:
            return await fetch_balance(self.client, credentials)
        version = self.audit_version()
        started = time.time()
        path = getattr(self, 'credential_path', self.folder / 'credentials.json')
        saved = path.stat().st_mtime if path.exists() else started
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO session_lifetimes(version,credential_saved,observed_since) VALUES (?,?,?)', (version, saved, started))
            rowid = self.db.execute('INSERT INTO session_queries(version,kind,started,outcome) VALUES (?,?,?,?)', (version, kind, started, 'running')).lastrowid
        outcome, reading, error = 'cancelled', None, None
        try:
            result = await fetch_balance(self.client, credentials)
            reading = result['updated']
            # Reading freshness is recorded separately from session validity.
            CHINA = timezone(timedelta(hours=8))
            updated = datetime.fromisoformat(reading).replace(tzinfo=CHINA)
            now = datetime.now(CHINA)
            outcome = 'stale' if now-updated > timedelta(hours=48) or updated > now+timedelta(minutes=10) else 'ok'
            with self.db:
                self.db.execute('UPDATE session_lifetimes SET last_success=? WHERE version=?', (time.time(), version))
            return result
        except (SessionExpired, WrongMeter) as exc:
            outcome = 'auth' if isinstance(exc, SessionExpired) else 'meter'
            error = type(exc).__name__
            column = 'first_auth_failure' if outcome == 'auth' else 'first_meter_failure'
            with self.db:
                self.db.execute(f'UPDATE session_lifetimes SET {column}=COALESCE({column},?) WHERE version=?', (time.time(), version))
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            outcome, error = 'error', type(exc).__name__
            raise
        finally:
            with self.db:
                self.db.execute('UPDATE session_queries SET finished=?,outcome=?,reading_time=?,error_type=? WHERE id=?', (time.time(), outcome, reading, error, rowid))

    async def keepalive(self, now):
        if not self.config.get('enabled', False):
            return
        allowed = {str(v) for v in self.config.get('keepalive_account_ids', [])}
        if str(getattr(self, 'uid', '')) not in allowed:
            return
        interval = max(900, int(self.config.get('keepalive_interval_minutes', 60)) * 60)
        async with self.lock:
            if self.state('suspended'):
                return
            row = self.db.execute('SELECT MAX(started) FROM session_queries WHERE version=?', (self.audit_version(),)).fetchone()
            if row[0] is not None and time.time() - row[0] < interval:
                return
            try:
                await self.audited_fetch(self.credentials(), 'keepalive')
            except (SessionExpired, WrongMeter) as exc:
                self.set_state('suspended', 'auth' if isinstance(exc, SessionExpired) else 'meter')
                self.set_state('keepalive_notice_pending', self.audit_version())
            except Exception:
                # Recorded above; wait a full interval rather than hammering upstream.
                pass
