import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import test_frontend_contract as fixtures
from app import activity
from app.db import db, transaction

class BootstrapTests(unittest.TestCase):
    def test_first_operation_can_be_store_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            env=dict(os.environ,DB_PATH=str(Path(tmp)/'first.db'))
            script="import sys; sys.path.insert(0,'backend'); from app.db import store_set,store_get; store_set('first',1); assert store_get('first')==1"
            subprocess.run([sys.executable,'-c',script],env=env,cwd=Path(__file__).resolve().parents[2],timeout=3,check=True,capture_output=True)

    def test_default_signing_key_is_not_a_public_development_key(self):
        env=dict(os.environ)
        env.pop('JWT_SECRET',None)
        script="import sys; sys.path.insert(0,'backend'); from app.config import settings; from app.auth import create_token,verify_token; secret=settings.jwt_secret; assert secret and secret!='dev-insecure-secret'; assert verify_token(create_token()); settings.jwt_secret='dev-insecure-secret'; forged=create_token(); settings.jwt_secret=secret; assert verify_token(forged) is None"
        subprocess.run([sys.executable,'-c',script],env=env,cwd=Path(__file__).resolve().parents[2],timeout=3,check=True,capture_output=True)

    def test_explicit_public_secret_requires_an_explicit_development_override(self):
        env=dict(os.environ,JWT_SECRET='dev-insecure-secret')
        env.pop('MYPLAYER_ALLOW_INSECURE_DEV',None)
        script="import sys; sys.path.insert(0,'backend'); import app.config"
        result=subprocess.run([sys.executable,'-c',script],env=env,cwd=Path(__file__).resolve().parents[2],timeout=3,capture_output=True)
        self.assertNotEqual(result.returncode,0)
        env['MYPLAYER_ALLOW_INSECURE_DEV']='1'
        subprocess.run([sys.executable,'-c',script],env=env,cwd=Path(__file__).resolve().parents[2],timeout=3,check=True,capture_output=True)

    def test_existing_playlist_schema_is_migrated_without_losing_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            env=dict(os.environ,DB_PATH=str(Path(tmp)/'old.db'))
            script="import os,sqlite3,sys; sys.path.insert(0,'backend'); c=sqlite3.connect(os.environ['DB_PATH']); c.execute('CREATE TABLE playlists(id INTEGER PRIMARY KEY,source TEXT NOT NULL,external_id TEXT NOT NULL,title TEXT NOT NULL,cover TEXT,total INTEGER NOT NULL DEFAULT 0,synced_at REAL NOT NULL,UNIQUE(source,external_id))'); c.execute(\"INSERT INTO playlists(source,external_id,title,synced_at) VALUES('vk','old','Existing',0)\"); c.commit(); c.close(); from app.db import db; row=db().execute('SELECT title,tracks_loaded FROM playlists').fetchone(); assert tuple(row)==('Existing',0)"
            subprocess.run([sys.executable,'-c',script],env=env,cwd=Path(__file__).resolve().parents[2],timeout=3,check=True,capture_output=True)

class ConcurrentFavoriteTests(unittest.TestCase):
    setUp=fixtures.ContractTests.setUp
    tearDown=fixtures.ContractTests.tearDown
    refresh=fixtures.ContractTests.refresh

    def test_concurrent_idempotent_likes_keep_one_favorite(self):
        self.refresh()
        with ThreadPoolExecutor(max_workers=12) as workers:
            results=list(workers.map(lambda i: activity.toggle_favorite('vk' if i%2 else 'soundcloud','1',True), range(48)))
        self.assertTrue(all(results))
        self.assertEqual(db().execute('SELECT COUNT(*) FROM favorites').fetchone()[0],1)

    def test_failed_transaction_rolls_back_all_of_its_writes(self):
        self.refresh()
        with self.assertRaises(RuntimeError):
            with transaction() as conn:
                conn.execute("INSERT INTO favorites VALUES(?,?,?,?)", ('rollback','vk','1',1))
                raise RuntimeError('abort this write')
        self.assertEqual(db().execute('SELECT COUNT(*) FROM favorites').fetchone()[0],0)
