"""Claim-type aware overclaim detection, faithful rewrites and the pair evaluation."""
import csv

import fake_llm
import pytest

import evaluate
import pipeline as pl

EVIDENCE = "Coffee drinking was associated with lower mortality in older adults."


def levels(**kw):
    base = {"causal": ["association", "association"], "scope": ["studied", "studied"],
            "certainty": ["tentative", "tentative"], "act": ["finding", "finding"]}
    base.update(kw)
    return base


@pytest.mark.parametrize("change,expected", [
    ({}, []),
    ({"causal": ["association", "causal"]}, ["correlation_to_causation"]),
    ({"causal": ["none", "causal"]}, ["correlation_to_causation"]),
    ({"causal": ["none", "association"]}, []),                     # not a causal overclaim
    ({"causal": ["causal", "association"]}, []),                   # weaker than the evidence is fine
    ({"scope": ["studied", "general"]}, ["subgroup_to_population"]),
    ({"certainty": ["tentative", "definitive"]}, ["preliminary_to_established"]),
    ({"act": ["finding", "recommendation"]}, ["finding_to_recommendation"]),
    ({"act": ["recommendation", "recommendation"]}, []),           # the paper itself recommends it
    ({"causal": [None, "causal"]}, []),                            # unknown rating never creates a distortion
    ({"causal": ["association", "causal"], "scope": ["studied", "general"]},
     ["correlation_to_causation", "subgroup_to_population"]),
])
def test_compare_levels(change, expected):
    assert pl.compare_levels(levels(**change)) == expected


def test_unknown_ratings_are_ignored():
    v = pl.Verdict(item_id="X1", verdict="SUPPORTED", evidence_ids=[], issue_type="", explanation="",
                   suggested_rewrite="", evidence_causal="weird", claim_causal="CAUSAL ")
    assert pl.claim_levels(v)["causal"] == [None, "causal"]
    assert pl.normalise_types(["Correlation to causation", "nonsense", "correlation_to_causation"]) == \
        ["correlation_to_causation"]


def test_cues_only_fire_when_the_evidence_lacks_them():
    assert [t for t, _ in pl.cue_types("Coffee lowers mortality.", EVIDENCE)] == ["correlation_to_causation"]
    assert pl.cue_types("Coffee lowers mortality.", "In the trial, coffee reduces mortality.") == []
    assert [t for t, _ in pl.cue_types("Adults should drink coffee.", EVIDENCE)] == ["finding_to_recommendation"]


def passages():
    return [pl.Passage(pid="P1-1", page=1, text=EVIDENCE),
            pl.Passage(pid="P2-1", page=2, text="The pilot trial with 30 students suggests the app may help.")]


def test_verify_types_a_causal_overclaim_and_passes_a_faithful_claim():
    items = [{"id": "R1", "part": "Press release", "text": "Coffee drinking lowers mortality in older adults."},
             {"id": "R2", "part": "Press release", "text": "Coffee drinking was linked to lower mortality in older adults."}]
    r1, r2 = pl.verify(fake_llm.FakeLLM(), passages(), items)
    assert r1["verdict"] == "EXAGGERATED" and r1["types"] == ["correlation_to_causation"]
    assert r1["levels"]["causal"] == ["association", "causal"]
    assert "association" in r1["explanation"] and r1["issue_type"] == "Correlation → causation"
    assert r2["verdict"] == "SUPPORTED" and r2["types"] == []


def test_faithful_rewrite_is_rechecked_and_keeps_strength():
    items = [{"id": "R1", "part": "Press release", "text": "Coffee drinking lowers mortality in older adults."}]
    res = pl.verify(fake_llm.FakeLLM(), passages(), items)
    pl.rewrite_flagged(fake_llm.FakeLLM(), fake_llm.FakeLLM(), passages(), res)
    r = res[0]
    assert r["rewrite"] == EVIDENCE
    assert r["rewrite_verified"] is True and r["rewrite_strength_kept"] is True and r["rewrite_rounds"] == 1


def test_strength_kept_rejects_needless_weakening():
    original = {"levels": levels(causal=["association", "causal"], scope=["general", "general"])}
    ok = {"levels": levels(causal=["association", "association"], scope=["general", "general"])}
    too_weak = {"levels": levels(causal=["association", "association"], scope=["general", "studied"])}
    still_causal = {"levels": levels(causal=["association", "causal"], scope=["general", "general"])}
    assert pl.strength_kept(original, ok) is True
    assert pl.strength_kept(original, too_weak) is False
    assert pl.strength_kept(original, still_causal) is False
    assert pl.strength_kept(original, {"levels": {}}) is None


def test_rewrite_failure_never_breaks_the_results(monkeypatch):
    res = pl.verify(fake_llm.FakeLLM(), passages(),
                    [{"id": "R1", "part": "Press release", "text": "Coffee drinking lowers mortality in older adults."}])
    fake_llm.FakeLLM.fail_next = 1
    with pytest.raises(RuntimeError):
        pl.rewrite_flagged(fake_llm.FakeLLM(), fake_llm.FakeLLM(), passages(), res)
    assert res[0]["verdict"] == "EXAGGERATED"                     # the check result is untouched


# ------------------------------------------------------------------ evaluation

def write_pairs(path, rows, delimiter=",", encoding="utf-8"):
    with open(path, "w", newline="", encoding=encoding) as fh:
        w = csv.writer(fh, delimiter=delimiter)
        w.writerow(["id", "paper", "page", "evidence", "claim", "type", "notes"])
        w.writerows(rows)


PAIRS = [
    ["EXAMPLE-1", "x", "1", "e", "c", "faithful", "ignored"],
    ["T1", "Coffee", "1", EVIDENCE, "Coffee drinking lowers mortality in older adults.", "correlation_to_causation", ""],
    ["T2", "Coffee", "1", EVIDENCE, "Coffee drinking was linked to lower mortality in older adults.", "faithful", ""],
    ["T3", "Coffee", "1", EVIDENCE, "Coffee drinking was linked to lower mortality in people.", "subgroup_to_population", ""],
    ["T4", "Coffee", "1", EVIDENCE, "Older adults should drink coffee.", "finding_to_recommendation", ""],
    ["T5", "App", "2", "The pilot trial with 30 students suggests the app may help.",
     "The app is proven to help students.", "preliminary_to_established", ""],
]


def test_load_pairs_handles_excel_style_csv(tmp_path):
    path = tmp_path / "pairs.csv"
    write_pairs(path, PAIRS, delimiter=";", encoding="cp1250")
    pairs = evaluate.load_pairs(str(path))
    assert [p["id"] for p in pairs] == ["T1", "T2", "T3", "T4", "T5"]
    assert pairs[1]["gold"] == [] and pairs[0]["gold"] == ["correlation_to_causation"]


def test_load_pairs_rejects_bad_rows(tmp_path):
    path = tmp_path / "bad.csv"
    write_pairs(path, [["T1", "p", "1", "e", "c", "causation"], ["T1", "p", "1", "e", "c", "faithful"],
                       ["T2", "p", "1", "", "c", "faithful"]])
    with pytest.raises(SystemExit, match="unknown type"):
        evaluate.load_pairs(str(path))


def test_wilson_interval():
    lo, hi = evaluate.wilson(0, 23)
    assert lo == 0 and abs(hi - 0.143) < 0.002
    lo, hi = evaluate.wilson(22, 25)
    assert abs(lo - 0.70) < 0.01 and abs(hi - 0.96) < 0.01


def row(pid, gold, ai_verdict="SUPPORTED", distortions=(), ai_types=(), cue_types=()):
    return {"id": pid, "gold": list(gold), "ai_verdict": ai_verdict, "verdict": ai_verdict,
            "distortions": list(distortions), "ai_types": list(ai_types), "cue_types": list(cue_types)}


def test_pair_metrics_per_type():
    rows = [row("1", ["correlation_to_causation"], distortions=["correlation_to_causation"]),
            row("2", ["correlation_to_causation"]),                                         # missed
            row("3", ["subgroup_to_population"], distortions=["correlation_to_causation"]),  # wrong type
            row("4", [], distortions=["correlation_to_causation"]),                         # false alarm
            row("5", [], cue_types=["finding_to_recommendation"])]
    m = evaluate.pair_metrics(rows, "ai_levels")
    c2c = m["per_type"]["correlation_to_causation"]
    assert (c2c["tp"], c2c["fp"], c2c["fn"]) == (1, 2, 1)
    assert c2c["precision"]["value"] == pytest.approx(1 / 3) and c2c["recall"]["value"] == 0.5
    assert m["detection_recall"]["value"] == pytest.approx(2 / 3) and m["false_alarm_rate"]["value"] == 0.5
    assert m["confusion"]["subgroup_to_population"]["correlation_to_causation"] == 1
    assert m["confusion"]["faithful"]["not flagged"] == 1
    cues = evaluate.pair_metrics(rows, "ai_levels_cues")
    assert cues["false_alarm_rate"]["value"] == 1.0                                         # cue adds a review flag


@pytest.fixture
def eval_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluate, "FREEZE", str(tmp_path / "freeze.json"))
    monkeypatch.setattr(evaluate, "CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(evaluate, "api_key", lambda: "test-key")
    path = tmp_path / "pairs.csv"
    write_pairs(path, PAIRS)
    return tmp_path, str(path)


def test_dev_run_compares_models_and_writes_report(eval_dirs):
    tmp, path = eval_dirs
    summary = evaluate.main(["--pairs", path, "--checker", "model-a", "model-b", "--repeats", "2",
                             "--out", str(tmp / "out"), "--baseline"])
    assert set(summary) == {"model-a", "model-b"}
    m = summary["model-a"]["ai_levels"][0]
    assert m["detection_recall"]["value"] == 1.0 and m["false_alarm_rate"]["value"] == 0.0
    assert all(m["per_type"][t]["recall"]["value"] == 1.0 for t in evaluate.TYPES)
    text = next((tmp / "out").glob("*.md")).read_text(encoding="utf-8")
    assert "Development set" in text and "Confusion matrix" in text and "Previous checker" in text
    assert "Passed the re-check on the first try: 100%" in text


def test_test_mode_requires_freeze_one_model_and_unchanged_file(eval_dirs, tmp_path):
    tmp, path = eval_dirs
    out = ["--out", str(tmp / "out")]
    with pytest.raises(SystemExit, match="not frozen"):
        evaluate.main(["--pairs", path, "--test", "--checker", "model-a", *out])
    evaluate.main(["--pairs", path, "--freeze"])
    with pytest.raises(SystemExit, match="exactly one checker"):
        evaluate.main(["--pairs", path, "--test", "--checker", "model-a", "model-b", *out])
    evaluate.main(["--pairs", path, "--test", "--checker", "model-a", *out])
    assert "HELD-OUT TEST SET" in next((tmp / "out").glob("*TEST*.md")).read_text(encoding="utf-8")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('T6,x,1,"e","c",faithful,\n')
    with pytest.raises(SystemExit, match="changed after it was frozen"):
        evaluate.main(["--pairs", path, "--test", "--checker", "model-a", *out])


def test_interrupted_run_stops_loudly_and_resumes_from_cache(eval_dirs):
    tmp, path = eval_dirs
    fake_llm.FakeLLM.fail_next = 1
    with pytest.raises(SystemExit, match="Run the same command again"):
        evaluate.main(["--pairs", path, "--checker", "model-a", "--workers", "1", "--out", str(tmp / "out")])
    assert not list((tmp / "out").glob("*.md")) if (tmp / "out").exists() else True
    fake_llm.FakeLLM.calls.clear()
    evaluate.main(["--pairs", path, "--checker", "model-a", "--workers", "1", "--out", str(tmp / "out")])
    checked_again = [c for c in fake_llm.FakeLLM.calls if c[0] == "Report"]
    assert 0 < len(checked_again) < 5 + 5                         # only the failed pair is checked again
