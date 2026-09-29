"""PostgreSQL storage for CiteFlow. Same functions as the file storage in store.py.

Used automatically when DATABASE_URL is set (Streamlit secrets or environment variable), e.g.
    postgresql://user:password@host:5432/dbname?sslmode=require
Tables are created on first use. Uploaded papers and videos are stored in the database too, and cached on the
local disk only while they are being used.
"""
import json
import os
import shutil
import time

import psycopg
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

import store

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username   TEXT PRIMARY KEY,
    salt       TEXT NOT NULL,
    hash       TEXT NOT NULL,
    iter       INTEGER NOT NULL,
    name       TEXT NOT NULL DEFAULT '',
    created    DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS channels (
    username   TEXT PRIMARY KEY,
    data       JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS projects (
    username   TEXT NOT NULL,
    pid        TEXT NOT NULL,
    meta       JSONB NOT NULL,
    state      JSONB NOT NULL,
    updated    DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (username, pid)
);
CREATE INDEX IF NOT EXISTS projects_by_user ON projects (username, updated DESC);
CREATE TABLE IF NOT EXISTS project_files (
    username   TEXT NOT NULL,
    pid        TEXT NOT NULL,
    name       TEXT NOT NULL,
    size       BIGINT NOT NULL,
    data       BYTEA NOT NULL,
    PRIMARY KEY (username, pid, name),
    FOREIGN KEY (username, pid) REFERENCES projects (username, pid) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS login_failures (
    key        TEXT NOT NULL,
    at         DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS login_failures_key ON login_failures (key, at);
CREATE TABLE IF NOT EXISTS events (
    at               DOUBLE PRECISION NOT NULL,
    username         TEXT NOT NULL DEFAULT '',
    kind             TEXT NOT NULL,
    model            TEXT NOT NULL DEFAULT '',
    action           TEXT NOT NULL DEFAULT '',
    ok               BOOLEAN NOT NULL,
    detail           TEXT NOT NULL DEFAULT '',
    latency_ms       INTEGER NOT NULL DEFAULT 0,
    prompt_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens    INTEGER NOT NULL DEFAULT 0,
    thinking_tokens  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS events_at ON events (at);
CREATE TABLE IF NOT EXISTS usage (
    day        TEXT NOT NULL,
    key        TEXT NOT NULL,
    count      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, key)
);
"""


class PgStore:
    def __init__(self, url: str):
        self.pool = ConnectionPool(url, min_size=1, max_size=6, timeout=15, open=True,
                                   check=ConnectionPool.check_connection,   # reconnect after the server sleeps
                                   kwargs={"row_factory": tuple_row, "connect_timeout": 10,
                                           "prepare_threshold": None})  # works through PgBouncer poolers too
        with self.pool.connection() as c:
            c.execute(SCHEMA)

    def close(self):
        self.pool.close()

    # ------------------------------------------------------------ helpers
    def _q(self, sql, params=(), one=False, many=False):
        with self.pool.connection() as c:
            cur = c.execute(sql, params)
            if one:
                return cur.fetchone()
            if many:
                return cur.fetchall()
            return None

    @staticmethod
    def _cache(username, pid=""):
        """Local working folder for a project's files (videos are written here by the app)."""
        user = store.re.sub(r"[^a-z0-9._@-]", "_", username)
        return os.path.join(store.DATA, "cache", user, pid)

    # ------------------------------------------------------------ accounts
    def create_user(self, username, password, display_name):
        username = (username or "").strip().lower()
        if not store.valid_username(username):
            return "Use 3–40 characters: letters, numbers, dot, dash, underscore or @."
        problem = store.password_problem(password, username)
        if problem:
            return problem
        salt = store.secrets.token_hex(16)
        with self.pool.connection() as c:
            cur = c.execute("INSERT INTO users (username, salt, hash, iter, name, created) VALUES (%s,%s,%s,%s,%s,%s) "
                            "ON CONFLICT (username) DO NOTHING",
                            (username, salt, store._hash(password, salt), store.ITERATIONS,
                             (display_name or username).strip()[:80], time.time()))
            if cur.rowcount == 0:
                return "This username is already taken."
        return None

    def check_user(self, username, password):
        username = (username or "").strip().lower()
        row = self._q("SELECT salt, hash, iter, name FROM users WHERE username=%s", (username,), one=True)
        if not row:
            store._hash(password or "", "00" * 16)       # same work, so timing does not reveal accounts
            return None
        salt, h, it, name = row
        if not store.secrets.compare_digest(h, store._hash(password or "", salt, it)):
            return None
        if it < store.ITERATIONS:
            new_salt = store.secrets.token_hex(16)
            self._q("UPDATE users SET salt=%s, hash=%s, iter=%s WHERE username=%s",
                    (new_salt, store._hash(password, new_salt), store.ITERATIONS, username))
        return {"username": username, "name": name or username}

    def is_registered(self, username):
        return self._q("SELECT 1 FROM users WHERE username=%s", ((username or "").strip().lower(),), one=True) is not None

    def delete_user(self, username):
        username = (username or "").strip().lower()
        with self.pool.connection() as c:
            c.execute("DELETE FROM projects WHERE username=%s", (username,))      # files cascade
            c.execute("DELETE FROM channels WHERE username=%s", (username,))
            c.execute("DELETE FROM users WHERE username=%s", (username,))
        shutil.rmtree(self._cache(username), ignore_errors=True)

    # ------------------------------------------------------------ sign-in protection
    def locked_for(self, *keys):
        keys = [k for k in keys if k]
        if not keys:
            return 0
        since = time.time() - store.LOCK_SECONDS
        rows = self._q("SELECT key, at FROM login_failures WHERE key = ANY(%s) AND at > %s ORDER BY at",
                       (keys, since), many=True)
        wait = 0
        for k in keys:
            times = [at for key, at in rows if key == k]
            limit = store.MAX_FAILS * 4 if k.startswith("ip:") else store.MAX_FAILS
            if len(times) >= limit:
                wait = max(wait, int(store.LOCK_SECONDS - (time.time() - times[-limit])))
        return max(0, wait)

    def record_failure(self, *keys):
        now = time.time()
        with self.pool.connection() as c:
            c.execute("DELETE FROM login_failures WHERE at < %s", (now - store.LOCK_SECONDS,))
            for k in keys:
                if k:
                    c.execute("INSERT INTO login_failures (key, at) VALUES (%s, %s)", (k, now))

    def clear_failures(self, key):
        self._q("DELETE FROM login_failures WHERE key=%s", (key,))

    # ------------------------------------------------------------ usage limits
    def use_quota(self, counters):
        today = time.strftime("%Y-%m-%d")
        keys = [k for k, _ in counters]
        with self.pool.connection() as c:                     # one transaction: check and count together
            c.execute("DELETE FROM usage WHERE day < %s", (today,))
            for k in keys:
                c.execute("INSERT INTO usage (day, key, count) VALUES (%s, %s, 0) ON CONFLICT DO NOTHING", (today, k))
            rows = dict(c.execute("SELECT key, count FROM usage WHERE day=%s AND key = ANY(%s) ORDER BY key FOR UPDATE",
                                  (today, keys)).fetchall())
            for k, lim in counters:
                if rows.get(k, 0) >= lim:
                    return k
            c.execute("UPDATE usage SET count = count + 1 WHERE day=%s AND key = ANY(%s)", (today, keys))
        return None

    def usage_today(self, key):
        row = self._q("SELECT count FROM usage WHERE day=%s AND key=%s", (time.strftime("%Y-%m-%d"), key), one=True)
        return row[0] if row else 0

    # ------------------------------------------------------------ monitoring events
    def log_event(self, event):
        e = store._event(event)
        with self.pool.connection() as c:
            c.execute("INSERT INTO events (at, username, kind, model, action, ok, detail, latency_ms, prompt_tokens, "
                      "output_tokens, thinking_tokens) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                      (e["at"], e["user"], e["kind"], e["model"], e["action"], e["ok"], e["detail"], e["latency_ms"],
                       e["prompt_tokens"], e["output_tokens"], e["thinking_tokens"]))
            if int(e["at"]) % 50 == 0:                       # now and then, remove old events
                c.execute("DELETE FROM events WHERE at < %s", (time.time() - store.EVENT_DAYS * 86400,))

    def events_since(self, days=30):
        rows = self._q("SELECT at, username, kind, model, action, ok, detail, latency_ms, prompt_tokens, output_tokens, "
                       "thinking_tokens FROM events WHERE at >= %s ORDER BY at", (time.time() - days * 86400,), many=True)
        return [dict(zip(store.EVENT_FIELDS, r, strict=True)) for r in rows]

    # ------------------------------------------------------------ channels
    def get_channels(self, username):
        row = self._q("SELECT data FROM channels WHERE username=%s", (username,), one=True)
        return row[0] if row else {}

    def save_channels(self, username, channels):
        self._q("INSERT INTO channels (username, data) VALUES (%s, %s) "
                "ON CONFLICT (username) DO UPDATE SET data = EXCLUDED.data", (username, Jsonb(channels)))

    # ------------------------------------------------------------ projects
    def project_path(self, username, pid):
        d = self._cache(username, pid)
        os.makedirs(d, exist_ok=True)
        return d

    def save_project(self, username, pid, meta, state, pdf_bytes=None, video_path=None):
        try:
            self._save_project(username, pid, meta, state, pdf_bytes, video_path)
        except psycopg.Error as e:            # e.g. database full or unreachable: the app treats it like a disk error
            raise OSError(f"Could not save the project to the database: {e}") from e

    def _save_project(self, username, pid, meta, state, pdf_bytes=None, video_path=None):
        d = self.project_path(username, pid)
        if video_path and os.path.exists(video_path):
            dst = os.path.join(d, os.path.basename(video_path))
            if os.path.abspath(video_path) != os.path.abspath(dst):
                shutil.copyfile(video_path, dst)
            state["video_file"] = os.path.basename(video_path)
        meta = dict(meta, updated=time.time())
        # make sure the state is plain JSON (same as the file storage)
        state = json.loads(json.dumps(state, ensure_ascii=False, default=str))
        with self.pool.connection() as c:
            c.execute("INSERT INTO projects (username, pid, meta, state, updated) VALUES (%s,%s,%s,%s,%s) "
                      "ON CONFLICT (username, pid) DO UPDATE SET meta=EXCLUDED.meta, state=EXCLUDED.state, "
                      "updated=EXCLUDED.updated", (username, pid, Jsonb(meta), Jsonb(state), meta["updated"]))
            have = dict(c.execute("SELECT name, size FROM project_files WHERE username=%s AND pid=%s",
                                  (username, pid)).fetchall())
            if pdf_bytes is not None and "source.pdf" not in have:
                c.execute("INSERT INTO project_files (username, pid, name, size, data) VALUES (%s,%s,%s,%s,%s)",
                          (username, pid, "source.pdf", len(pdf_bytes), pdf_bytes))
            for name in os.listdir(d):                       # videos and subtitles the app wrote to the folder
                p = os.path.join(d, name)
                if name == "source.pdf" or not os.path.isfile(p):
                    continue
                size = os.path.getsize(p)
                if have.get(name) != size:
                    with open(p, "rb") as fh:
                        data = fh.read()
                    c.execute("INSERT INTO project_files (username, pid, name, size, data) VALUES (%s,%s,%s,%s,%s) "
                              "ON CONFLICT (username, pid, name) DO UPDATE SET size=EXCLUDED.size, data=EXCLUDED.data",
                              (username, pid, name, size, data))

    def list_projects(self, username):
        rows = self._q("SELECT pid, meta FROM projects WHERE username=%s ORDER BY updated DESC", (username,), many=True)
        return [dict(meta, id=pid) for pid, meta in rows]

    def load_project(self, username, pid):
        row = self._q("SELECT state FROM projects WHERE username=%s AND pid=%s", (username, pid), one=True)
        if not row:
            return None, None, None
        state = row[0]
        files = self._q("SELECT name, data FROM project_files WHERE username=%s AND pid=%s", (username, pid), many=True)
        d = self.project_path(username, pid)
        pdf = None
        for name, data in files:
            if name == "source.pdf":
                pdf = bytes(data)
                continue
            p = os.path.join(d, os.path.basename(name))
            if not os.path.exists(p) or os.path.getsize(p) != len(data):
                with open(p, "wb") as fh:                    # restore videos to the local working folder
                    fh.write(data)
        video = os.path.join(d, state["video_file"]) if state.get("video_file") else None
        return state, pdf, video if video and os.path.exists(video) else None

    def delete_project(self, username, pid):
        self._q("DELETE FROM projects WHERE username=%s AND pid=%s", (username, pid))
        shutil.rmtree(self._cache(username, pid), ignore_errors=True)

    def purge_old_projects(self, days, special=None):
        special = special or {}
        now = time.time()
        with self.pool.connection() as c:
            gone = []
            for user, limit in special.items():
                gone += c.execute("DELETE FROM projects WHERE username=%s AND updated > 0 AND updated < %s "
                                  "RETURNING username, pid", (user, now - limit * 86400)).fetchall()
            gone += c.execute("DELETE FROM projects WHERE NOT (username = ANY(%s)) AND updated > 0 AND updated < %s "
                              "RETURNING username, pid", (list(special), now - days * 86400)).fetchall()
        for user, pid in gone:
            shutil.rmtree(self._cache(user, pid), ignore_errors=True)
        return len(gone)

    # ------------------------------------------------------------ status
    def stats(self):
        with self.pool.connection() as c:
            return {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                    for t in ("users", "channels", "projects", "project_files", "login_failures", "usage", "events")}
