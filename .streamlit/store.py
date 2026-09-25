"""Local accounts, connected channels and saved projects (JSON files under ./data)."""
import hashlib
import json
import os
import re
import secrets
import shutil
import time

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


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


def _hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 120_000).hex()


def valid_username(u):
    return bool(re.fullmatch(r"[A-Za-z0-9._@-]{3,40}", u or ""))


def create_user(username, password, display_name):
    username = (username or "").strip().lower()
    if not valid_username(username):
        return "Use 3–40 characters: letters, numbers, dot, dash, underscore or @."
    if len(password or "") < 6:
        return "The password needs at least 6 characters."
    users = _read(USERS, {})
    if username in users:
        return "This username is already taken."
    salt = secrets.token_hex(16)
    users[username] = {"salt": salt, "hash": _hash(password, salt),
                       "name": (display_name or username).strip(), "created": time.time()}
    _write(USERS, users)
    return None


def check_user(username, password):
    username = (username or "").strip().lower()
    u = _read(USERS, {}).get(username)
    if not u or not secrets.compare_digest(u["hash"], _hash(password or "", u["salt"])):
        return None
    return {"username": username, "name": u.get("name", username)}

# ------------------------------------------------------------------ channels

CHANNELS = ["linkedin", "facebook", "instagram", "x", "blog"]


def _udir(username):
    return os.path.join(DATA, "users", re.sub(r"[^a-z0-9._@-]", "_", username))


def get_channels(username):
    return _read(os.path.join(_udir(username), "channels.json"), {})


def save_channels(username, channels):
    _write(os.path.join(_udir(username), "channels.json"), channels)

# ------------------------------------------------------------------ projects


def _pdir(username, pid=""):
    return os.path.join(_udir(username), "projects", pid)


def project_path(username, pid):
    d = _pdir(username, pid)
    os.makedirs(d, exist_ok=True)
    return d


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


def list_projects(username):
    root = _pdir(username)
    out = []
    if os.path.isdir(root):
        for pid in os.listdir(root):
            m = _read(os.path.join(root, pid, "meta.json"), None)
            if m:
                out.append(dict(m, id=pid))
    return sorted(out, key=lambda m: m.get("updated", 0), reverse=True)


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


def delete_project(username, pid):
    shutil.rmtree(_pdir(username, pid), ignore_errors=True)


def new_project_id():
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
