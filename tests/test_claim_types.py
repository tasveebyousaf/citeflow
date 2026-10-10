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


def test_strength_kept_compares_within_the_recheck():
    original = {"types": ["correlation_to_causation"]}
    exact = {"levels": levels(causal=["association", "association"])}
    too_weak = {"levels": levels(causal=["association", "none"])}
    still_causal = {"levels": levels(causal=["association", "causal"])}
    assert pl.strength_kept(original, exact) is True
    assert pl.strength_kept(original, too_weak) is False
    assert pl.strength_kept(original, still_causal) is False
    assert pl.strength_kept(original, {"levels": {}}) is None
    assert pl.strength_kept({"types": []}, exact) is None


def test_main_type_prefers_the_confirmed_ai_choice():
    both = ["correlation_to_causation", "finding_to_recommendation"]
    assert pl.main_first(both, "finding_to_recommendation", [])[0] == "finding_to_recommendation"
    assert pl.main_first(both, "subgroup_to_population", ["finding_to_recommendation"])[0] == "finding_to_recommendation"
    assert pl.main_first(both, "", []) == both                          # scale order as last resort
    # the main choice also appearing later in the AI's own list must not lose its first place
    assert pl.main_first(both, "finding_to_recommendation", both)[0] == "finding_to_recommendation"


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


@pytest.mark.parametrize("claim,missing", [
    ("Participants ate 0.34 more cups of fruit.", []),          # paper writes 0⋅34 (raised dot)
    ("Participants ate 0.22 more cups of vegetables.", []),     # paper writes 0·22 (middle dot)
    ("The review covered 36,768 participants.", []),            # paper writes 36 768 (space separator)
    ("The relative risk was 1.20.", []),
    ("The programme reached over 830,000 residents.", []),     # honest rounding: 830,049 is over 830,000
    ("The programme reached 830,000 residents.", ["830,000"]),  # bare rounding: a real problem
    ("In 2019, 340 people took part.", []),                     # a year is not merged with the next number
])
def test_number_check_understands_journal_number_styles(claim, missing):
    evidence = ("Participants consumed 0⋅34 cups more fruit and 0·22 cups more vegetables; 36 768 participants; "
                "RR=1⋅20; an estimated 830 049 individuals; in 2019 340 people")
    assert pl.number_check(claim, evidence) == missing


PAPER = ("Fruit intake rose by 0·34 cups and vegetable intake by 0,22 cups. In total 830 049 people were reached by "
         "701 changes. Accuracy was 14.7% (95% CI 12.1–17.3). Eight of the 25 sites were rural. The effect was −0.5. "
         "Half of the participants were women. Overall 1.2 million meals were served in 2019.")


@pytest.mark.parametrize("claim,problems", [
    ("Fruit intake rose by 0.34 cups and vegetables by 0.22 cups.", []),            # dot styles and decimal comma
    ("830,049 people were reached through 701 changes.", []),                        # space thousands separator
    ("Accuracy was nearly 15%.", []),                                                 # honest: 14.7 is under 15
    ("Accuracy was about 15%.", []),                                                  # honest approximation
    ("Accuracy was over 15%.", ["15%"]),                   # wrong direction: 14.7 is not over 15
    ("Accuracy was 15%.", ["15%"]),                                                   # bare rounding
    ("8 of the twenty-five sites were rural.", []),                                   # words <-> digits
    ("The effect was a decrease of 0.5.", []),                                        # sign ignored
    ("50% of the participants were women.", []),                                      # 'half' <-> 50%
    ("More than a million meals were served.", []),                                   # 1.2 million is more than a million
    ("1.2 million meals were served in 2019.", []),                                   # scale words
    ("Accuracy reached 19.7%.", ["19.7%"]),                                           # simply wrong
    ("One of the sites was in the 21st century. #AI2026 https://x.org/7", []),         # generic words, ordinals, tags, links
])
def test_number_rule_compares_values(claim, problems):
    assert pl.number_check(claim, "", PAPER) == problems


def test_number_message_names_the_paper_value():
    assert pl.number_issues("Accuracy was 15%.", "", PAPER) == ["15% (the paper says 14.7%)"]
    assert pl.number_issues("Reached 830,000 people.", "", PAPER) == ["830,000 (the paper says 830,049)"]


def test_number_found_elsewhere_in_paper_is_not_flagged():
    cited = "Fruit intake rose by 0·34 cups."
    assert pl.number_check("0.34 cups more fruit, reaching 830,049 people.", cited, PAPER) == []
    assert pl.number_check("0.34 cups more fruit, reaching 830,049 people.", cited) == ["830,049"]


def test_number_elsewhere_in_paper_must_be_about_the_same_thing():
    passages = ["Fruit intake rose by 0·34 cups a day among participants.",
                "Table 4: response rates 1·5 and 0·3 in the county survey."]
    assert pl.number_check("Fruit intake rose by 0.34 cups a day.", "", passages) == []
    assert pl.number_check("Fruit intake rose by 0.3 cups a day.", "", passages) == ["0.3"]      # 0·3 is about something else
