"""Accounts, sign-in protection, usage limits and projects. Runs against files, or PostgreSQL if TEST_DATABASE_URL is set."""
import json
import os
import threading
import time

import store


def test_accounts_and_password_policy():
    assert store.create_user("anna@x.hu", "Debrecen2026", "Anna") is None
    assert store.create_user("anna@x.hu", "Debrecen2026", "Anna") == "This username is already taken."
    assert "8 characters" in store.create_user("b@x.hu", "short1", "B")
    assert "letter and one number" in store.create_user("b@x.hu", "onlyletters", "B")
    assert "too easy" in store.create_user("b@x.hu", "password1", "B")
    assert store.check_user("anna@x.hu", "Debrecen2026") == {"username": "anna@x.hu", "name": "Anna"}
    assert store.check_user("anna@x.hu", "wrong") is None
    assert store.check_user("nobody@x.hu", "Debrecen2026") is None
    assert store.is_registered("anna@x.hu")


def test_team_password_hashes():
    h = store.make_hash("Team2026pass")
    assert h.startswith("pbkdf2_sha256$600000$")
    assert store.check_hash_string(h, "Team2026pass") and not store.check_hash_string(h, "x")
    assert store.check_hash_string("citeflow2026", "citeflow2026")          # legacy plain text still works


def test_lockout_per_account_and_ip():
    for _ in range(5):
        store.record_failure("anna@x.hu", "ip:1.2.3.4")
    assert store.locked_for("anna@x.hu") > 0
    assert store.locked_for("ip:1.2.3.4") == 0                           # IP limit is 20
    store.clear_failures("anna@x.hu")
    assert store.locked_for("anna@x.hu") == 0


def test_quota_blocks_and_does_not_count_blocked():
    for _ in range(3):
        assert store.use_quota([("run:u", 3), ("run:all", 100)]) is None
    assert store.use_quota([("run:u", 3), ("run:all", 100)]) == "run:u"
    assert store.usage_today("run:all") == 3


def test_quota_is_exact_under_concurrency():
    results = []
    threads = [threading.Thread(target=lambda: results.append(store.use_quota([("run:c", 10)]))) for _ in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count(None) == 10 and store.usage_today("run:c") == 10


def test_projects_roundtrip_isolation_and_delete(pdf_bytes):
    store.create_user("anna@x.hu", "Debrecen2026", "Anna")
    pid = store.new_project_id()
    folder = store.project_path("anna@x.hu", pid)
    with open(os.path.join(folder, "video.mp4"), "wb") as fh:
        fh.write(b"VIDEO" * 100)
    store.save_project("anna@x.hu", pid, {"headline": "H"}, {"release": "é ő", "video_file": "video.mp4"}, pdf_bytes=pdf_bytes)
    assert [p["id"] for p in store.list_projects("anna@x.hu")] == [pid]
    state, pdf, video = store.load_project("anna@x.hu", pid)
    assert state["release"] == "é ő" and pdf == pdf_bytes and video and os.path.exists(video)
    assert store.list_projects("bob@x.hu") == [] and store.load_project("bob@x.hu", pid) == (None, None, None)
    store.delete_project("anna@x.hu", pid)
    assert store.list_projects("anna@x.hu") == []


def test_delete_user_removes_everything(pdf_bytes):
    store.create_user("anna@x.hu", "Debrecen2026", "Anna")
    store.save_channels("anna@x.hu", {"x": "https://x.com/a"})
    store.save_project("anna@x.hu", "p1", {"headline": "H"}, {"a": 1}, pdf_bytes=pdf_bytes)
    store.delete_user("anna@x.hu")
    assert not store.is_registered("anna@x.hu")
    assert store.list_projects("anna@x.hu") == [] and store.get_channels("anna@x.hu") == {}


def test_legacy_hash_is_upgraded():
    salt = "ab" * 16
    legacy = store._hash("Oldpass123", salt, store.LEGACY_ITERATIONS)
    if store.backend() == "postgresql":
        with store._pg().pool.connection() as c:
            c.execute("INSERT INTO users VALUES (%s,%s,%s,%s,%s,%s)", ("old@x.hu", salt, legacy, 120000, "Old", 0))
    else:
        store._write(store.USERS, {"old@x.hu": {"salt": salt, "hash": legacy, "name": "Old"}})
    assert store.check_user("old@x.hu", "Oldpass123")
    assert store.check_user("old@x.hu", "Oldpass123")                    # still works after the upgrade


def _age(username, pid, days_old):
    """Pretends a project was last changed `days_old` days ago."""
    meta = {"headline": pid, "updated": time.time() - days_old * 86400}
    if store.backend() == "postgresql":
        with store._pg().pool.connection() as c:
            c.execute("UPDATE projects SET meta = meta || %s::jsonb, updated = %s WHERE username=%s AND pid=%s",
                      (json.dumps({"updated": meta["updated"]}), meta["updated"], username, pid))
    else:
        store._write(os.path.join(store._pdir(username, pid), "meta.json"), meta)


def test_retention_deletes_only_old_projects(pdf_bytes):
    for user in ("anna@x.hu", "demo@citeflow.app"):
        for pid, days in (("fresh", 2), ("month", 30), ("old", 400)):
            store.save_project(user, pid, {"headline": pid}, {"a": 1}, pdf_bytes=pdf_bytes)
            _age(user, pid, days)
    removed = store.purge_old_projects(365, {"demo@citeflow.app": 14})
    assert removed == 3                                                   # anna: old; demo: month + old
    assert sorted(p["id"] for p in store.list_projects("anna@x.hu")) == ["fresh", "month"]
    assert [p["id"] for p in store.list_projects("demo@citeflow.app")] == ["fresh"]
