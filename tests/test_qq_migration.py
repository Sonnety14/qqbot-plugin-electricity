import importlib.util
import sqlite3
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('qq_migration',Path(__file__).parents[1]/'integration/site_service/electricity_qq.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

def test_migration_and_rolling_cooldown():
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.executescript('''CREATE TABLE users(id INTEGER PRIMARY KEY);
      INSERT INTO users VALUES(7);
      CREATE TABLE campus_qq_bindings(user_id INTEGER,qq TEXT);
      CREATE TABLE electric_jobs(user_id INTEGER PRIMARY KEY REFERENCES users(id),qq TEXT,state TEXT,payload TEXT,token_hash TEXT);
      CREATE TABLE electric_bindings(user_id INTEGER PRIMARY KEY REFERENCES users(id),qq TEXT,version TEXT);
      CREATE TABLE electric_preferences(user_id INTEGER PRIMARY KEY REFERENCES users(id),alerts_enabled INTEGER);
      CREATE TABLE electric_binding_cooldowns(user_id INTEGER PRIMARY KEY REFERENCES users(id),binding_version TEXT,committed_at REAL);
      INSERT INTO electric_bindings VALUES(7,'10007','fixture');
      INSERT INTO electric_preferences VALUES(7,0);
      INSERT INTO electric_binding_cooldowns VALUES(7,'fixture',100000);''')
    with db:module.initialize(db)
    assert tuple(db.execute('SELECT * FROM electric_qq_bindings').fetchone())==(7,'10007','fixture')
    assert db.execute('SELECT alerts_enabled FROM electric_qq_preferences').fetchone()[0]==0
    assert not module.quota_available(db,7,186399)
    assert module.quota_available(db,7,186400)
    with pytest.raises(sqlite3.IntegrityError),db:module.consume_quota(db,7,'early',186399)
    with pytest.raises(RuntimeError),db:
        module.consume_quota(db,7,'failed',186400)
        raise RuntimeError('simulated binding failure')
    assert module.next_binding_at(db,7)==186400
    with db:module.consume_quota(db,7,'new',186400)
    with db:module.initialize(db)
    assert module.next_binding_at(db,7)==272800
    assert not db.execute('PRAGMA foreign_key_check').fetchall()
    db.close()
