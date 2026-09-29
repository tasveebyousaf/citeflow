"""Accounts, sign-in protection, usage limits, connected channels and saved projects.

Two interchangeable storage backends with the same functions:
- PostgreSQL (store_pg.py), used automatically when DATABASE_URL is set in Streamlit secrets or the environment;
- JSON files under ./data, used otherwise (local development, demos).
"""
import functools
import hashlib
import json
import os
import re
import secrets
import shutil
import threading
import time

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# ------------------------------------------------------------------ backend selection

_PG, _PG_URL = None, None


def database_url():
    """DATABASE_URL from the environment or Streamlit secrets ("" = use JSON files)."""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        try:
            import streamlit as st
            if "DATABASE_URL" in st.secrets:
                url = st.secrets["DATABASE_URL"]
        except Exception:
            url = ""
    url = str(url or "").strip().strip('"\'')
    return "" if url.startswith("paste-your") else url


def _pg():
    global _PG, _PG_URL
    url = database_url()
    if not url:
        return None
    if _PG is None or url != _PG_URL:
        import store_pg
        _PG, _PG_URL = store_pg.PgStore(url), url
    return _PG


def backend():
    return "postgresql" if _pg() else "files"


def _dispatch(fn):
    """Run the PostgreSQL version of a storage function when a database is configured."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        db = _pg()
        return getattr(db, fn.__name__)(*args, **kwargs) if db else fn(*args, **kwargs)
    return wrapper



def _read(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)

# ------------------------------------------------------------------ accounts

USERS = os.path.join(DATA, "users.json")
ITERATIONS = 600_000          # PBKDF2-SHA256 work factor (OWASP 2023 recommendation)
LEGACY_ITERATIONS = 120_000   # accounts created before the security update
_LOCK = threading.RLock()     # Streamlit serves sessions in threads: serialise read-modify-write of JSON files
COMMON = {"password", "password1", "12345678", "123456789", "qwerty123", "11111111", "iloveyou", "admin123",
          "citeflow", "citeflow1", "letmein1", "welcome1", "abc12345", "passw0rd"}


def _hash(password, salt, iterations=ITERATIONS):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations).hex()


def make_hash(password):
    """Portable hash string for secrets.toml: pbkdf2_sha256$iterations$salt$hash"""
    salt = secrets.token_hex(16)
    return f"pbkdf2_sha256${ITERATIONS}${salt}${_hash(password, salt)}"


def check_hash_string(stored, password):
    """Verify a team password from secrets: either a pbkdf2_sha256$... hash or (legacy) plain text."""
    stored, password = str(stored or ""), str(password or "")
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, it, salt, h = stored.split("$")
            return secrets.compare_digest(h, _hash(password, salt, int(it)))
        except ValueError:
            return False
    return bool(stored) and secrets.compare_digest(stored.encode(), password.encode())


def valid_username(u):
    return bool(re.fullmatch(r"[A-Za-z0-9._@-]{3,40}", u or ""))


def password_problem(password, username=""):
    """Returns a message if the password is too weak, else None."""
    p = password or ""
    if len(p) < 8:
        return "The password needs at least 8 characters."
    if len(p) > 128:
        return "The password can have at most 128 characters."
    if not (re.search(r"[A-Za-z]", p) and re.search(r"\d", p)):
        return "Use at least one letter and one number."
    if p.lower() in COMMON or (username and p.lower() == username.lower()):
        return "This password is too easy to guess. Please choose another one."
    return None


@_dispatch
def create_user(username, password, display_name):
    username = (username or "").strip().lower()
    if not valid_username(username):
        return "Use 3–40 characters: letters, numbers, dot, dash, underscore or @."
    problem = password_problem(password, username)
    if problem:
        return problem
    with _LOCK:
        users = _read(USERS, {})
        if username in users:
            return "This username is already taken."
        salt = secrets.token_hex(16)
        users[username] = {"salt": salt, "hash": _hash(password, salt), "iter": ITERATIONS,
                           "name": (display_name or username).strip()[:80], "created": time.time()}
        _write(USERS, users)
    return None


@_dispatch
def check_user(username, password):
    username = (username or "").strip().lower()
    u = _read(USERS, {}).get(username)
    if not u:
        _hash(password or "", "00" * 16)          # same work as a real check, so timing does not reveal accounts
        return None
    it = u.get("iter", LEGACY_ITERATIONS)
    if not secrets.compare_digest(u["hash"], _hash(password or "", u["salt"], it)):
        return None
    if it < ITERATIONS:                         # upgrade old hashes on successful sign-in
        with _LOCK:
            users = _read(USERS, {})
            if username in users:
                salt = secrets.token_hex(16)
                users[username].update(salt=salt, hash=_hash(password, salt), iter=ITERATIONS)
                _write(USERS, users)
    return {"username": username, "name": u.get("name", username)}


@_dispatch
def is_registered(username):
    return (username or "").strip().lower() in _read(USERS, {})


@_dispatch
def delete_user(username):
    """Deletes the account and every file that belongs to it (projects, PDFs, videos, channels)."""
    username = (username or "").strip().lower()
    with _LOCK:
        users = _read(USERS, {})
        users.pop(username, None)
        _write(USERS, users)
    shutil.rmtree(_udir(username), ignore_errors=True)

# ------------------------------------------------------------------ sign-in protection

FAILS = os.path.join(DATA, "security", "login_failures.json")
MAX_FAILS, LOCK_SECONDS = 5, 600


def _recent(times, window):
    now = time.time()
    return [t for t in times if now - t < window]


@_dispatch
def locked_for(*keys):
    """Seconds left before sign-in is allowed again for any of these keys (username, ip:...), else 0."""
    data = _read(FAILS, {})
    wait = 0
    for k in keys:
        if not k:
            continue
        times = _recent(data.get(k, []), LOCK_SECONDS)
        limit = MAX_FAILS * 4 if k.startswith("ip:") else MAX_FAILS
        if len(times) >= limit:
            wait = max(wait, int(LOCK_SECONDS - (time.time() - times[-limit])))
    return max(0, wait)


@_dispatch
def record_failure(*keys):
    with _LOCK:
        data = {k: _recent(v, LOCK_SECONDS) for k, v in _read(FAILS, {}).items()}
        data = {k: v for k, v in data.items() if v}
        for k in keys:
            if k:
                data.setdefault(k, []).append(time.time())
        _write(FAILS, data)


@_dispatch
def clear_failures(key):
    with _LOCK:
        data = _read(FAILS, {})
        if data.pop(key, None) is not None:
            _write(FAILS, data)

# ------------------------------------------------------------------ usage limits (protect the AI quota)

USAGE = os.path.join(DATA, "security", "usage.json")


@_dispatch
def use_quota(counters):
    """counters: list of (key, limit). Counts one use against every key for today, but only if ALL are under
    their limit. Returns the key that is exhausted, or None when the use was allowed and counted."""
    today = time.strftime("%Y-%m-%d")
    with _LOCK:
        data = _read(USAGE, {})
        day = data.get(today, {})
        for key, limit in counters:
            if day.get(key, 0) >= limit:
                return key
        for key, _ in counters:
            day[key] = day.get(key, 0) + 1
        _write(USAGE, {today: day})          # keep only today
    return None


@_dispatch
def usage_today(key):
    return _read(USAGE, {}).get(time.strftime("%Y-%m-%d"), {}).get(key, 0)


# ------------------------------------------------------------------ monitoring events (AI calls and errors)

EVENT_FIELDS = ("at", "user", "kind", "model", "action", "ok", "detail", "latency_ms",
                "prompt_tokens", "output_tokens", "thinking_tokens")
EVENT_DAYS = 90                         # events older than this are deleted


def _event(event):
    e = {k: event.get(k) for k in EVENT_FIELDS}
    e["at"] = e["at"] or time.time()
    e["kind"] = e["kind"] or "ai_call"
    e["ok"] = bool(e["ok"]) if e["ok"] is not None else True
    for k in ("latency_ms", "prompt_tokens", "output_tokens", "thinking_tokens"):
        e[k] = int(e[k] or 0)
    for k in ("user", "model", "action", "detail"):
        e[k] = str(e[k] or "")[:300]
    return e


@_dispatch
def log_event(event):
    """Appends one monitoring event (JSON lines, one file per month)."""
    e = _event(event)
    folder = os.path.join(DATA, "logs")
    with _LOCK:
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, time.strftime("events-%Y-%m.jsonl", time.localtime(e["at"]))), "a",
                  encoding="utf-8") as fh:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
        oldest = time.strftime("events-%Y-%m", time.localtime(time.time() - EVENT_DAYS * 86400 - 31 * 86400))
        for name in os.listdir(folder):
            if name.startswith("events-") and name[:14] < oldest:
                os.remove(os.path.join(folder, name))


@_dispatch
def events_since(days=30):
    since = time.time() - days * 86400
    folder = os.path.join(DATA, "logs")
    out = []
    if os.path.isdir(folder):
        for name in sorted(os.listdir(folder)):
            if not name.startswith("events-"):
                continue
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                for line in fh:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if e.get("at", 0) >= since:
                        out.append(e)
    return out

# ------------------------------------------------------------------ channels

CHANNELS = ["linkedin", "facebook", "instagram", "x", "blog"]


def _udir(username):
    return os.path.join(DATA, "users", re.sub(r"[^a-z0-9._@-]", "_", username))


@_dispatch
def get_channels(username):
    return _read(os.path.join(_udir(username), "channels.json"), {})


@_dispatch
def save_channels(username, channels):
    _write(os.path.join(_udir(username), "channels.json"), channels)

# ------------------------------------------------------------------ projects


def _pdir(username, pid=""):
    return os.path.join(_udir(username), "projects", pid)


@_dispatch
def project_path(username, pid):
    d = _pdir(username, pid)
    os.makedirs(d, exist_ok=True)
    return d


@_dispatch
def save_project(username, pid, meta, state, pdf_bytes=None, video_path=None):
    d = _pdir(username, pid)
    os.makedirs(d, exist_ok=True)
    if pdf_bytes is not None and not os.path.exists(os.path.join(d, "source.pdf")):
        with open(os.path.join(d, "source.pdf"), "wb") as fh:
            fh.write(pdf_bytes)
    if video_path and os.path.exists(video_path):
        dst = os.path.join(d, os.path.basename(video_path))
        if os.path.abspath(video_path) != os.path.abspath(dst):
            shutil.copyfile(video_path, dst)
        state["video_file"] = os.path.basename(video_path)
    meta = dict(meta, updated=time.time())
    _write(os.path.join(d, "meta.json"), meta)
    _write(os.path.join(d, "state.json"), state)


@_dispatch
def list_projects(username):
    root = _pdir(username)
    out = []
    if os.path.isdir(root):
        for pid in os.listdir(root):
            m = _read(os.path.join(root, pid, "meta.json"), None)
            if m:
                out.append(dict(m, id=pid))
    return sorted(out, key=lambda m: m.get("updated", 0), reverse=True)


@_dispatch
def load_project(username, pid):
    d = _pdir(username, pid)
    state = _read(os.path.join(d, "state.json"), None)
    if state is None:
        return None, None, None
    try:
        with open(os.path.join(d, "source.pdf"), "rb") as fh:
            pdf = fh.read()
    except OSError:
        pdf = None
    video = os.path.join(d, state["video_file"]) if state.get("video_file") else None
    return state, pdf, video if video and os.path.exists(video) else None


@_dispatch
def delete_project(username, pid):
    shutil.rmtree(_pdir(username, pid), ignore_errors=True)


@_dispatch
def purge_old_projects(days, special=None):
    """Deletes projects not changed for `days` days. special: {username: days} for accounts with their own
    period (e.g. the shared demo account). Projects without a recorded update time are kept. Returns the count."""
    special = special or {}
    now = time.time()
    root = os.path.join(DATA, "users")
    removed = 0
    if not os.path.isdir(root):
        return 0
    users = {re.sub(r"[^a-z0-9._@-]", "_", u): u for u in _read(USERS, {})}
    users.update({re.sub(r"[^a-z0-9._@-]", "_", u): u for u in special})
    for folder in os.listdir(root):
        username = users.get(folder, folder)
        limit = special.get(username, days)
        proot = os.path.join(root, folder, "projects")
        for pid in (os.listdir(proot) if os.path.isdir(proot) else []):
            updated = (_read(os.path.join(proot, pid, "meta.json"), {}) or {}).get("updated")
            if updated and now - float(updated) > limit * 86400:
                shutil.rmtree(os.path.join(proot, pid), ignore_errors=True)
                removed += 1
    return removed


def new_project_id():
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
