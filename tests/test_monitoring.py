"""Monitoring: AI calls and errors are recorded; only admins see the Admin page."""
from test_app import click, new_app, run, seed_project, sign_in

import store


def test_store_events_roundtrip():
    store.log_event({"kind": "ai_call", "user": "a", "model": "m", "action": "Report", "ok": True,
                     "latency_ms": 900, "prompt_tokens": 1000, "output_tokens": 200})
    store.log_event({"kind": "error", "user": "a", "action": "render video", "ok": False, "detail": "boom"})
    ev = store.events_since(1)
    assert [e["kind"] for e in ev] == ["ai_call", "error"]
    assert ev[0]["prompt_tokens"] == 1000 and ev[1]["ok"] is False


def test_ai_calls_are_logged_with_user(app_path, pdf_bytes):
    store.create_user("anna@unideb.hu", "Debrecen2026", "Anna")
    at = seed_project(sign_in(new_app(app_path), "anna@unideb.hu", "Debrecen2026"), pdf_bytes)
    click(at, "Shorter & punchier")
    calls = [e for e in store.events_since(1) if e["kind"] == "ai_call"]
    assert calls and all(e["user"] == "anna@unideb.hu" for e in calls)
    assert {e["action"] for e in calls} >= {"Content", "Report"} and calls[0]["prompt_tokens"] > 0


def test_errors_are_logged(app_path, pdf_bytes):
    import fake_llm
    store.create_user("anna@unideb.hu", "Debrecen2026", "Anna")
    at = seed_project(sign_in(new_app(app_path), "anna@unideb.hu", "Debrecen2026"), pdf_bytes)
    fake_llm.FakeLLM.fail_next = 1
    click(at, "Shorter & punchier")
    errors = [e for e in store.events_since(1) if not e["ok"]]
    assert any(e["kind"] == "error" and e["action"] == "rework content" for e in errors)
    assert any(e["kind"] == "ai_call" and "simulated failure" in e["detail"] for e in errors)


def test_admin_page_only_for_admins(app_path, pdf_bytes):
    store.create_user("anna@unideb.hu", "Debrecen2026", "Anna")
    at = new_app(app_path)
    at.secrets["ADMINS"] = ["boss@unideb.hu"]
    sign_in(at, "anna@unideb.hu", "Debrecen2026")
    assert "Admin" not in [b.label for b in at.button]
    at.session_state["page"] = "Admin"
    run(at)
    assert at.session_state["page"] == "Studio"                           # redirected

    store.create_user("boss@unideb.hu", "Debrecen2026", "Boss")
    store.log_event({"kind": "ai_call", "user": "anna@unideb.hu", "model": "m", "action": "Report", "ok": True,
                     "prompt_tokens": 2000, "output_tokens": 500})
    store.log_event({"kind": "error", "user": "anna@unideb.hu", "action": "render video", "ok": False, "detail": "boom"})
    boss = new_app(app_path)
    boss.secrets["ADMINS"] = ["boss@unideb.hu"]
    boss.secrets["pricing"] = {"m": {"input": 1.0, "output": 2.0}}
    sign_in(boss, "boss@unideb.hu", "Debrecen2026")
    click(boss, "Admin")
    metrics = {m.label: m.value for m in boss.metric}
    assert metrics["AI calls"] == "1" and metrics["Estimated cost"] == "$0.00" and metrics["Tokens in"] == "2k" and metrics["Tokens out"] == "500"
    assert len(boss.dataframe) >= 3                                        # by model, by user, errors
