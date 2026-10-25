"""Independent QQ electricity identities; preserve legacy worker IDs and ciphertext."""
import sqlite3

TABLES=('electric_jobs','electric_bindings','electric_preferences','electric_binding_cooldowns')

def initialize(db):
    db.execute('BEGIN IMMEDIATE')
    db.execute('CREATE TABLE IF NOT EXISTS electric_qq_accounts(id INTEGER PRIMARY KEY, qq TEXT UNIQUE NOT NULL, auth_version INTEGER NOT NULL DEFAULT 1)')
    db.execute('CREATE TABLE IF NOT EXISTS electric_qq_migrations(version INTEGER PRIMARY KEY)')
    if db.execute('SELECT 1 FROM electric_qq_migrations WHERE version=1').fetchone():return
    # Account IDs stay stable so existing encrypted worker files and daily dedup survive.
    candidates={}
    for table in ('electric_jobs','electric_bindings'):
        for r in db.execute('SELECT user_id,qq FROM '+table):candidates[r['user_id']]=r['qq']
    for r in db.execute('SELECT c.user_id,q.qq FROM electric_binding_cooldowns c JOIN campus_qq_bindings q ON q.user_id=c.user_id'):
        candidates.setdefault(r['user_id'],r['qq'])
    for uid,qq in candidates.items():
        db.execute('INSERT INTO electric_qq_accounts(id,qq) VALUES (?,?)',(uid,qq))
    for table in TABLES:
        new=table.replace('electric_','electric_qq_',1)
        sql=db.execute('SELECT sql FROM sqlite_master WHERE name=?',(table,)).fetchone()[0]
        sql=sql.replace(table,new,1).replace('REFERENCES users(id)','REFERENCES electric_qq_accounts(id)')
        db.execute(sql)
        db.execute('INSERT INTO '+new+' SELECT * FROM '+table+' WHERE user_id IN (SELECT id FROM electric_qq_accounts)')
    # In-flight website requests are no longer accepted. Existing completed bindings stay.
    db.execute("UPDATE electric_qq_jobs SET state='expired',payload=NULL,token_hash='' WHERE state!='done'")
    db.execute('INSERT INTO electric_qq_migrations VALUES(1)')

def next_binding_at(db,uid):
    row=db.execute('SELECT committed_at FROM electric_qq_binding_cooldowns WHERE user_id=?',(uid,)).fetchone()
    return row[0]+86400 if row else 0

def quota_available(db,uid,now):return now>=next_binding_at(db,uid)

def consume_quota(db,uid,version,now):
    n=db.execute('''INSERT INTO electric_qq_binding_cooldowns VALUES(?,?,?)
      ON CONFLICT(user_id) DO UPDATE SET binding_version=excluded.binding_version,committed_at=excluded.committed_at
      WHERE excluded.committed_at>=electric_qq_binding_cooldowns.committed_at+86400''',(uid,version,now)).rowcount
    if not n:raise sqlite3.IntegrityError('Binding cooldown active')
