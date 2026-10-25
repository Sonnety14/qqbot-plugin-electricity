"""Room input and rolling 24-hour binding cooldown.

Not enabled by importing this module: callers must use an IMMEDIATE transaction
and consume the quota only when a verified binding is committed.
"""
import re
import sqlite3
from datetime import datetime, timedelta, timezone

CHINA = timezone(timedelta(hours=8))


def parse_room(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError('请按示例填写：天佑斋 012001')
    match = re.fullmatch(r'(天佑斋|鸿哲斋)\s+([0-9]{2})([1-9]|1[0-9])([0-9]{3})', value.strip())
    if not match:
        raise ValueError('请按示例填写：天佑斋 012001')
    area, building, floor, room = match.groups()
    if not all(int(part) > 0 for part in (building, floor, room)):
        raise ValueError('楼栋、楼层和房间号必须大于零')
    if area == '鸿哲斋' and int(building) not in (2, 3):
        raise ValueError('目前仅支持鸿哲斋 2、3 栋')
    return {
        'room': f'{area} {building}{floor}{room}',
        'area': area, 'building': int(building), 'floor': int(floor),
        'room_number': int(room), 'room_code': floor + room,
        'payment_category': '天佑斋电控缴费',
    }


def month_key(timestamp):
    return datetime.fromtimestamp(timestamp, CHINA).strftime('%Y-%m')


def initialize_quota(db):
    db.execute('''CREATE TABLE IF NOT EXISTS electric_binding_months (
        user_id INTEGER NOT NULL REFERENCES users(id), month TEXT NOT NULL,
        binding_version TEXT NOT NULL, committed_at REAL NOT NULL,
        PRIMARY KEY(user_id, month))''')
    db.execute('''CREATE TABLE IF NOT EXISTS electric_binding_cooldowns (
        user_id INTEGER PRIMARY KEY REFERENCES users(id),
        binding_version TEXT NOT NULL, committed_at REAL NOT NULL)''')
    # Preserve the most recent successful old binding, without extending its age.
    db.execute('''INSERT OR IGNORE INTO electric_binding_cooldowns
        SELECT user_id,binding_version,MAX(committed_at)
        FROM electric_binding_months GROUP BY user_id''')


def next_binding_at(db, user_id):
    row = db.execute('SELECT committed_at FROM electric_binding_cooldowns WHERE user_id=?', (user_id,)).fetchone()
    return float(row[0]) + 86400 if row else 0


def quota_available(db, user_id, timestamp):
    return timestamp >= next_binding_at(db, user_id)


def consume_quota(db, user_id, version, timestamp):
    """Run in the SAME transaction as binding commit; unique key guards races.

    No cleanup on unbind: removing a binding must not replenish the quota.
    Failed validation must never call this function.
    """
    changed = db.execute('''INSERT INTO electric_binding_cooldowns VALUES (?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET binding_version=excluded.binding_version,
        committed_at=excluded.committed_at
        WHERE excluded.committed_at >= electric_binding_cooldowns.committed_at + 86400''',
        (user_id, version, timestamp)).rowcount
    if not changed:
        raise sqlite3.IntegrityError('Binding cooldown is still active')
