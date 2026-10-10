"""End-to-end interface tests with Streamlit's AppTest (the AI model is the fake from fake_llm.py)."""
import time

import pytest
from streamlit.testing.v1 import AppTest

import cards
import pipeline as pl
import store

TIMEOUT = 120


def run(at):
    at.run(timeout=TIMEOUT)
    assert not at.exception, at.exception[0].message
    return at


def click(at, label):
    buttons = [b for b in at.button if b.label == label]
    assert buttons, f"no button {label!r}: {[b.label for b in at.button]}"
    buttons[0].click()
    return run(at)


def new_app(app_path):
    at = AppTest.from_file(app_path, default_timeout=TIMEOUT)
    at.secrets["GEMINI_API_KEY"] = "test-key"                       # never the real secrets.toml values
    at.secrets["users"] = {"demo@citeflow.app": "citeflow2026"}
    return run(at)


def sign_in(at, user, password):
    at.text_input[0].input(user)
    at.text_input[1].input(password)
    return click(at, "Sign in")


@pytest.fixture
def user_app(app_path):
    store.create_user("anna@unideb.hu", "Debrecen2026", "Anna")
    return sign_in(new_app(app_path), "anna@unideb.hu", "Debrecen2026")


def seed_project(at, pdf_bytes):
    """Puts a generated, checked project into the session exactly like the Studio's Create button does."""
    S = at.session_state
    passages, title = pl.extract_passages(pdf_bytes)
    llm = pl.LLM("k", "fake-model")
    c = pl.generate_content(llm, passages, "English")
    S["pdf_bytes"], S["passages"], S["figures"], S["doc_title"], S["doc_name"] = pdf_bytes, passages, [], title, "paper.pdf"
    S["content"], S["release"], S["lang"], S["posts"] = c, c.press_release, "English", c.posts.model_dump()
    S["results"] = pl.verify(llm, passages, pl.build_items(c.headline, c.press_release, c.scenes, S["posts"], c.visual))
    v = pl.safe_visual(c.visual, S["results"])
    S["cards"] = {k: cards.post_image(v, c.card_title, c.institution, None, size)
                  for k, size in (("square", (1080, 1080)), ("wide", (1200, 675)), ("portrait", (1080, 1350)))}
    S["person"], S["photo"], S["chat"], S["accepted"] = "", None, [], []
    S["pid"], S["created"] = store.new_project_id(), time.strftime("%Y-%m-%d %H:%M")
    S["page"] = "Studio"
    return run(at)


def flagged(at):
    return [r["text"] for r in at.session_state["results"] if r["verdict"] in pl.FLAGGED]


# ------------------------------------------------------------------ sign-in and accounts

def test_login_page_and_wrong_password(app_path):
    at = new_app(app_path)
    assert "user" not in at.session_state
    sign_in(at, "nobody@x.hu", "Wrong12345")
    assert any("not correct" in e.value for e in at.error)


def test_lockout_after_five_failures(app_path):
    store.create_user("anna@unideb.hu", "Debrecen2026", "Anna")
    at = new_app(app_path)
    for _ in range(5):
        sign_in(at, "anna@unideb.hu", "wrong-pass1")
    sign_in(at, "anna@unideb.hu", "Debrecen2026")
    assert "user" not in at.session_state and any("Too many failed" in e.value for e in at.error)


def test_sign_up_needs_consent_and_strong_password(app_path):
    at = new_app(app_path)
    [t for t in at.text_input if t.key == "su_id"][0].input("new@unideb.hu")
    [t for t in at.text_input if t.key == "su_p1"][0].input("weak")
    [t for t in at.text_input if t.key == "su_p2"][0].input("weak")
    click(at, "Create account")
    assert any("privacy" in e.value for e in at.error)
    at.checkbox[0].check()
    click(at, "Create account")
    assert any("8 characters" in e.value for e in at.error)
    [t for t in at.text_input if t.key == "su_p1"][0].input("Debrecen2026")
    [t for t in at.text_input if t.key == "su_p2"][0].input("Debrecen2026")
    click(at, "Create account")
    assert at.session_state["user"] == "new@unideb.hu" and store.is_registered("new@unideb.hu")


def test_delete_account_needs_password(user_app, pdf_bytes):
    store.save_project("anna@unideb.hu", "p1", {"headline": "H"}, {"a": 1}, pdf_bytes=pdf_bytes)
    user_app.session_state["page"] = "Account"
    run(user_app)
    user_app.text_input[0].input("wrong")
    click(user_app, "Delete everything")
    assert store.is_registered("anna@unideb.hu")
    user_app.text_input[0].input("Debrecen2026")
    click(user_app, "Delete everything")
    assert not store.is_registered("anna@unideb.hu") and store.list_projects("anna@unideb.hu") == []


def test_demo_account_restrictions(app_path, pdf_bytes):
    at = sign_in(new_app(app_path), "demo@citeflow.app", "citeflow2026")
    assert at.session_state["user"] == "demo@citeflow.app"
    assert any("shared demo account" in c.value for c in at.caption)
    store.save_project("demo@citeflow.app", "p9", {"headline": "Demo"}, {"a": 1}, pdf_bytes=pdf_bytes)
    at.session_state["page"] = "Projects"
    run(at)
    assert all(b.disabled for b in at.button if b.label == "Delete")
    at.session_state["page"] = "Account"
    run(at)
    assert "Delete everything" not in [b.label for b in at.button]


# ------------------------------------------------------------------ review workflow

def test_review_fix_keep_and_persist(user_app, pdf_bytes):
    at = seed_project(user_app, pdf_bytes)
    assert "The system proves that AI can replace experts." in flagged(at)
    fix = [b for b in at.button if b.label.startswith("Fix all")]
    assert fix
    fix[0].click()
    run(at)
    assert "The system proves that AI can replace experts." not in flagged(at)
    assert "The system is designed to assist experts." in at.session_state["release"]
    # saved to the project list and reopened in a fresh session
    assert len(store.list_projects("anna@unideb.hu")) == 1                 # saved to the project list


def test_reopen_project_in_new_session(user_app, pdf_bytes, app_path):
    seed_project(user_app, pdf_bytes)
    at = sign_in(new_app(app_path), "anna@unideb.hu", "Debrecen2026")
    at.session_state["page"] = "Projects"
    run(at)
    click(at, "Open")
    assert at.session_state["release"] and len(at.session_state["results"]) > 5


def test_refine_with_feedback_rechecks(user_app, pdf_bytes):
    at = seed_project(user_app, pdf_bytes)
    quick = [b for b in at.button if b.label == "Shorter & punchier"]
    assert quick
    quick[0].click()
    run(at)
    assert at.session_state["posts"]["linkedin"] == "Shorter LinkedIn post. #AI"
    assert [n for n, _ in __import__("fake_llm").FakeLLM.calls].count("Report") >= 2      # checked again


def test_insights_plan_and_usage_limit(user_app, pdf_bytes):
    at = seed_project(user_app, pdf_bytes)
    at.session_state["page"] = "Insights"
    run(at)
    click(at, "Create publishing plan")
    assert at.session_state["plan"].platforms[0].platform == "LinkedIn"
    for _ in range(20):
        store.use_quota([("run:anna@unideb.hu", 15)])
    click(at, "Refresh plan")
    assert any("today's limit" in w.value for w in at.warning)


def test_all_pages_render(user_app, pdf_bytes):
    at = seed_project(user_app, pdf_bytes)
    for page in ("Projects", "Insights", "Channels", "Account", "Studio"):
        at.session_state["page"] = page
        run(at)


def test_checker_is_independent_model(user_app, pdf_bytes):
    import fake_llm
    at = seed_project(user_app, pdf_bytes)
    fake_llm.FakeLLM.calls.clear()
    [b for b in at.button if b.label == "Shorter & punchier"][0].click()
    run(at)
    assert at.session_state["models"] == {"writer": "fake-model", "checker": "fake-model-2"}
    assert ("Report", "fake-model-2") in fake_llm.FakeLLM.calls and ("Report", "fake-model") not in fake_llm.FakeLLM.calls
    assert any("independent checker" in c.value for c in at.caption)


def test_privacy_note_states_retention(app_path):
    at = new_app(app_path)
    assert any("365 days" in m.value and "14 days" in m.value for m in at.markdown)


# ------------------------------------------------------------------ claim-type distortions in the interface

def typed_flag(at):
    """Turns the first flagged result into a typed correlation -> causation distortion without a rewrite."""
    r = next(r for r in at.session_state["results"] if r["verdict"] in pl.FLAGGED)
    r.update(types=["correlation_to_causation"], rewrite="", rewrite_verified=None,
             levels={"causal": ["association", "causal"], "scope": ["studied", "studied"],
                     "certainty": ["tentative", "tentative"], "act": ["finding", "finding"]})
    return r


def test_flagged_sentence_shows_type_and_levels(user_app, pdf_bytes):
    at = seed_project(user_app, pdf_bytes)
    typed_flag(at)
    run(at)
    shown = " ".join(m.value for m in at.markdown)
    assert "Distortion type" in shown and "Correlation → causation" in shown
    assert "Paper: an association" in shown and "Claim: cause and effect" in shown


def test_write_faithful_version_on_demand(user_app, pdf_bytes):
    at = seed_project(user_app, pdf_bytes)
    r = typed_flag(at)
    run(at)
    click(at, "Write faithful version")
    fixed = next(x for x in at.session_state["results"] if x["id"] == r["id"])
    assert fixed["rewrite"] and fixed["rewrite_verified"] is True
    assert any("Re-checked by the independent checker" in c.value for c in at.caption)
    click(at, "Use faithful version")
    assert fixed["rewrite"] in at.session_state["release"] or r["part"] != "Press release"


def test_stress_test_lists_distortions_with_fixes(user_app, pdf_bytes):
    at = seed_project(user_app, pdf_bytes)
    click(at, "Run stress test")
    hyped, hres = at.session_state["hype"]
    assert any(r["verdict"] in pl.FLAGGED for r in hres)
    assert any("What the checker found" in m.value for m in at.markdown)


def test_every_flag_gets_a_label():
    from ui.results import distortion_label
    assert distortion_label({"verdict": "EXAGGERATED", "types": ["finding_to_recommendation"]}) == \
        ("Measured outcome → recommendation", True)
    assert distortion_label({"verdict": "UNSUPPORTED", "issue_type": "not in source"})[0] == "Not in the paper → stated as fact"
    assert distortion_label({"verdict": "NEEDS_REVIEW", "rule_flags": ["number(s) not found in cited passages: 15%"]})[0] == \
        "Exact figure → altered figure"
    assert distortion_label({"verdict": "EXAGGERATED", "issue_type": "removed limitation"})[0] == "Qualified result → caveat removed"
    assert distortion_label({"verdict": "EXAGGERATED", "explanation": "The paper does not call it a breakthrough."})[0] == \
        "Measured result → hyped"
    assert distortion_label({"verdict": "EXAGGERATED"}) == ("Supported strength → overstated", False)
    assert distortion_label({"verdict": "UNCHECKED"})[0] == "Not checked → check again"


def test_remove_and_edit_recheck_run_before_the_page_is_drawn(user_app, pdf_bytes):
    import fake_llm
    at = seed_project(user_app, pdf_bytes)
    bad = "The system proves that AI can replace experts."
    assert bad in flagged(at)
    target = next(r for r in at.session_state["results"] if r["text"] == bad)
    [b for b in at.button if b.key == f"rm_{target['id']}"][0].click()
    run(at)
    assert bad not in at.session_state["release"] and bad not in flagged(at)
    assert "pending_action" not in at.session_state
    fake_llm.FakeLLM.calls.clear()
    [t for t in at.text_area if t.label == "Press release"][0].input(at.session_state["release"] + " It cures cancer.")
    click(at, "Re-check my edits")
    assert "It cures cancer." in at.session_state["release"]
    assert any(n == "Report" for n, _ in fake_llm.FakeLLM.calls)              # checked again


def test_verified_faithful_version_is_used_without_checking_again(user_app, pdf_bytes):
    import fake_llm
    at = seed_project(user_app, pdf_bytes)
    r = next(x for x in at.session_state["results"] if x["verdict"] in pl.FLAGGED and x["part"] == "Press release")
    good = "The team built a screening system designed to assist cytology experts."
    r.update(rewrite=good, rewrite_verified=True, rewrite_check={"verdict": "SUPPORTED", "levels": {}})
    run(at)
    fake_llm.FakeLLM.calls.clear()
    [b for b in at.button if b.key == f"fix_{r['id']}"][0].click()
    run(at)
    assert good in at.session_state["release"]
    fixed = next(x for x in at.session_state["results"] if x["id"] == r["id"])
    assert fixed["verdict"] == "SUPPORTED" and fixed["text"] == good
    assert not fake_llm.FakeLLM.calls                                     # no AI calls: instant


def test_unverified_faithful_version_is_still_checked(user_app, pdf_bytes):
    import fake_llm
    at = seed_project(user_app, pdf_bytes)
    r = next(x for x in at.session_state["results"] if x["verdict"] in pl.FLAGGED and x["part"] == "Press release")
    r.update(rewrite="The system is designed to assist experts.", rewrite_verified=False)
    run(at)
    fake_llm.FakeLLM.calls.clear()
    [b for b in at.button if b.key == f"fix_{r['id']}"][0].click()
    run(at)
    assert any(n == "Report" for n, _ in fake_llm.FakeLLM.calls)          # checked again
