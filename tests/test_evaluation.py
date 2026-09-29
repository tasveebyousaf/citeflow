"""The accuracy evaluation (evaluate.py) and the independent checker model."""
import json

from conftest import make_pdf

import evaluate
import pipeline as pl


def test_claims_file_is_well_formed():
    data = evaluate.load_claims()
    claims = data["claims"]
    assert len(claims) >= 40
    assert {c["label"] for c in claims} == {"ok", "wrong"}
    assert len({c["id"] for c in claims}) == len(claims)
    assert all(c["paper"] in data["papers"] for c in claims)


def test_metrics_and_report(tmp_path):
    (tmp_path / "paper.pdf").write_bytes(make_pdf())
    claims = [
        {"id": "T1", "paper": "paper.pdf", "label": "ok", "kind": "faithful",
         "text": "The system reached an average accuracy of 88.8% on 339 smears."},
        {"id": "T2", "paper": "paper.pdf", "label": "wrong", "kind": "overclaim",
         "text": "The system can replace experts."},
        {"id": "T3", "paper": "paper.pdf", "label": "wrong", "kind": "number_change",
         "text": "The system reached an average accuracy of 98.8%."},
        {"id": "T4", "paper": "missing.pdf", "label": "ok", "kind": "faithful", "text": "Skipped."},
    ]
    rows = evaluate.run(pl.LLM("k", "fake-model"), str(tmp_path), claims)
    assert [r["id"] for r in rows] == ["T1", "T2", "T3"]                    # missing paper skipped
    m = evaluate.metrics(rows)
    assert m["recall"] == 1.0 and m["false_alarm_rate"] == 0.0 and m["precision"] == 1.0
    assert m["per_kind"]["number_change"] == {"caught": 1, "total": 1}      # caught by the number rule
    text = evaluate.report(m, "writer", "checker", "auto", {"checker": 1})
    assert "Recall (distortions caught): 100%" in text


def test_main_writes_results(tmp_path, monkeypatch):
    (tmp_path / "paper.pdf").write_bytes(make_pdf())
    claims = tmp_path / "claims.json"
    claims.write_text(json.dumps({"papers": {"paper.pdf": "Test"}, "claims": [
        {"id": "T1", "paper": "paper.pdf", "label": "wrong", "kind": "overclaim", "text": "It cures cancer."}]}))
    monkeypatch.setattr(evaluate, "api_key", lambda: "test-key")
    m = evaluate.main(["--papers", str(tmp_path), "--claims", str(claims), "--out", str(tmp_path / "out")])
    assert m["recall"] == 1.0
    assert list((tmp_path / "out").glob("*.md")) and list((tmp_path / "out").glob("*.json"))


def test_checker_chain_is_independent_by_default():
    chain = ["gemini-3-flash", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-flash-latest"]
    assert pl.checker_chain(chain)[0] == "gemini-2.5-flash"
    assert pl.checker_chain(chain, "same")[0] == "gemini-3-flash"
    assert pl.checker_chain(chain, "gemini-2.5-pro")[0] == "gemini-2.5-pro"
    assert pl.checker_chain(["only-one"])[0] == "only-one"                  # falls back to the writer


def test_busy_service_skips_paper_instead_of_crashing(tmp_path, monkeypatch):
    import fake_llm
    (tmp_path / "a.pdf").write_bytes(make_pdf())
    (tmp_path / "b.pdf").write_bytes(make_pdf())
    claims = [{"id": "T1", "paper": "a.pdf", "label": "wrong", "kind": "overclaim", "text": "It cures cancer."},
              {"id": "T2", "paper": "b.pdf", "label": "wrong", "kind": "overclaim", "text": "It cures cancer."}]
    monkeypatch.setattr(evaluate, "BUSY_WAITS", (0,))
    real = pl.verify
    calls = {"n": 0}

    def busy_for_first_paper(llm, passages, items):
        calls["n"] += 1
        if calls["n"] <= 2:                                              # paper a: busy twice -> skipped
            raise RuntimeError("503 UNAVAILABLE high demand")
        return real(llm, passages, items)

    monkeypatch.setattr(pl, "verify", busy_for_first_paper)
    skipped = []
    rows = evaluate.run(fake_llm.FakeLLM(), str(tmp_path), claims, skipped)
    assert skipped == ["a.pdf"] and [r["id"] for r in rows] == ["T2"]
    text = evaluate.report(evaluate.metrics(rows), "w", "c", "auto", {"c": 1}, skipped)
    assert "Incomplete run" in text
