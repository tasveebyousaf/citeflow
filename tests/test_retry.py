"""Quota-aware retries: error classification, waits, the exhausted-model registry and fallback order."""

import pytest
from fake_llm import REAL_LIST_MODELS, REAL_LLM

import pipeline as pl


@pytest.fixture(autouse=True)
def clean_registry():
    pl._exhausted.clear()
    yield
    pl._exhausted.clear()


DAILY = ("429 RESOURCE_EXHAUSTED. Quota exceeded for metric: generate_content_free_tier_requests, "
         "limit: 250, quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier")
MINUTE = ("429 RESOURCE_EXHAUSTED. Quota exceeded, quotaId: GenerateRequestsPerMinutePerProjectPerModel. "
          "Please retry in 37.2s. 'retryDelay': '37s'")
CREDITS = "402 PAYMENT_REQUIRED. Your prepayment credits are depleted."


@pytest.mark.parametrize("msg,kind", [
    (CREDITS, "credits"),
    (DAILY, "daily_quota"),
    (MINUTE, "rate_limit"),
    ("404 NOT_FOUND. models/gemini-9 is not found", "not_found"),
    ("400 INVALID_ARGUMENT. Thinking level is not supported for this model", "thinking"),
    ("503 UNAVAILABLE. The model is overloaded", "transient"),
    ("500 INTERNAL", "transient"),
    ("400 INVALID_ARGUMENT. API key not valid", "fatal"),
    ("RemoteProtocolError: Server disconnected without sending a response.", "transient"),
    ("ConnectError: [Errno 11001] getaddrinfo failed", "transient"),
])
def test_classify_error(msg, kind):
    assert pl.classify_error(msg) == kind


def test_retry_delay_parses_both_formats():
    assert pl.retry_delay("'retryDelay': '37s'") == 37
    assert pl.retry_delay("Please retry in 12.5s.") == pytest.approx(12.5)
    assert pl.retry_delay("503 UNAVAILABLE") is None


def test_backoff_grows_and_is_capped():
    assert 2 <= pl.backoff(0) <= 3
    assert 4 <= pl.backoff(1) <= 5
    assert pl.backoff(10) <= 21


def test_exhausted_registry_expires():
    pl.mark_exhausted("m", now=1000)
    assert pl.is_exhausted("m", now=1000 + 10)
    assert not pl.is_exhausted("m", now=1000 + pl.EXHAUSTED_COOLDOWN + 1)
    assert not pl.is_exhausted("other", now=1000)


class Schema:
    __name__ = "Schema"


def make_llm(script, models=("a", "b")):
    """An LLM whose API answers come from a script: {model: [error text or "ok", ...]}."""
    waits, events = [], []
    llm = REAL_LLM("test-key", models[0], fallbacks=list(models[1:]), on_call=events.append, sleep=waits.append)

    def once(model, prompt, schema, temperature):
        step = script[model].pop(0)
        if step == "ok":
            return f"answer from {model}"
        raise RuntimeError(step)

    llm._once = once
    return llm, waits, events


def test_daily_quota_skips_model_without_waiting():
    llm, waits, _ = make_llm({"a": [DAILY], "b": ["ok"]})
    assert llm.json_call("p", Schema) == "answer from b"
    assert waits == []
    assert pl.is_exhausted("a")
    assert llm.last_model == "b"


def test_rate_limit_waits_the_suggested_delay_then_succeeds():
    llm, waits, _ = make_llm({"a": [MINUTE, "ok"], "b": []})
    assert llm.json_call("p", Schema) == "answer from a"
    assert len(waits) == 1 and 37 <= waits[0] <= 38


def test_transient_errors_back_off_then_fall_back():
    busy = "503 UNAVAILABLE"
    llm, waits, events = make_llm({"a": [busy, busy, busy, busy, busy, busy], "b": ["ok"]})
    assert llm.json_call("p", Schema) == "answer from b"
    assert len(waits) == 2                       # 3 attempts on "a" = 2 waits, then fallback
    assert [e["ok"] for e in events] == [False, False, False, True]


def test_credits_stop_immediately():
    llm, waits, _ = make_llm({"a": [CREDITS], "b": ["ok"]})
    with pytest.raises(pl.CreditsExhausted):
        llm.json_call("p", Schema)
    assert waits == []


def test_all_models_exhausted_gives_clear_error():
    llm, _, _ = make_llm({"a": [DAILY], "b": [DAILY]})
    with pytest.raises(pl.QuotaExhausted):
        llm.json_call("p", Schema)


def test_unknown_error_is_not_retried():
    llm, waits, _ = make_llm({"a": ["400 INVALID_ARGUMENT. API key not valid"], "b": ["ok"]})
    with pytest.raises(RuntimeError, match="API key"):
        llm.json_call("p", Schema)
    assert waits == []


def test_thinking_setting_is_dropped_once():
    llm, waits, _ = make_llm({"a": ["400 Thinking level is not supported", "ok"], "b": []})
    assert llm.json_call("p", Schema) == "answer from a"
    assert llm.fast is False and waits == []


CHAIN = ["gemini-3-flash", "gemini-2.5-pro", "gemini-3-flash-lite", "gemini-flash-latest"]


def test_checker_chain_never_falls_back_to_lite_or_latest():
    for setting in ("auto", "same", "gemini-2.5-pro"):
        chain = pl.checker_chain(CHAIN, setting)
        assert not any("lite" in m or "latest" in m for m in chain[1:])


def test_checker_chain_strict_uses_one_model():
    assert pl.checker_chain(CHAIN, "gemini-2.5-pro", strict=True) == ["gemini-2.5-pro"]
    assert pl.checker_chain(CHAIN, "auto", strict=True) == ["gemini-2.5-pro"]


REAL_LIST = ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-pro-latest", "gemini-2.5-flash-lite", "gemini-2.5-flash-image", "gemini-3-flash-preview", "gemini-3.1-pro-preview", "gemini-3.1-flash-lite", "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-omni-flash-preview", "gemini-omni-1.1-flash", "gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.8-flash", "gemini-3.8-flash-tts", "gemini-3.1-flash-live-preview"]


def test_real_model_list_gives_sensible_chains(monkeypatch):
    class M:
        def __init__(self, n):
            self.name, self.supported_actions = "models/" + n, ["generateContent"]

    class Client:
        def __init__(self, api_key=None):
            self.models = self

        def list(self):
            return [M(n) for n in REAL_LIST]

    from google import genai
    monkeypatch.setattr(genai, "Client", Client)
    writer = pl.model_chain("key", REAL_LIST_MODELS("key"))
    assert writer[:3] == ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash"]
    assert not any(x in m for m in writer for x in ("omni", "tts", "image", "live"))
    chk = pl.checker_chain(writer, "gemini-3.1-pro-preview")
    assert chk[0] == "gemini-3.1-pro-preview"
    assert not any("lite" in m or "latest" in m for m in chk)
    assert pl.checker_chain(writer, "gemini-3.1-pro-preview", strict=True) == ["gemini-3.1-pro-preview"]


def test_writer_never_uses_the_fixed_checker_model():
    chain = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash"]
    assert pl.writer_chain(chain, "gemini-3.8-flash")[0] == "gemini-3.7-flash"
    assert "gemini-3.8-flash" not in pl.writer_chain(chain, "gemini-3.8-flash")
    assert pl.writer_chain(chain, "auto") == chain
    assert pl.writer_chain(["only"], "only") == ["only"]                  # never left without a model


def test_dropped_connection_is_retried():
    class RemoteProtocolError(Exception):
        pass

    waits, calls = [], {"n": 0}
    llm = REAL_LLM("test-key", "a", fallbacks=[], sleep=waits.append)

    def once(model, prompt, schema, temperature):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RemoteProtocolError("Server disconnected without sending a response.")
        return "answer"

    llm._once = once
    assert llm.json_call("p", Schema) == "answer" and len(waits) == 1


def test_server_deadline_is_retried():
    assert pl.classify_error("504 DEADLINE_EXCEEDED. Deadline expired before operation could complete.") == "transient"
