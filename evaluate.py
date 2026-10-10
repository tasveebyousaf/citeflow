"""Measure how accurately CiteFlow's fact-checker catches distorted claims.

Runs the labelled statements in evaluation/claims.json through exactly the same checking code as the app
(AI verdict + deterministic rules) and reports:
  recall       = share of distorted statements that were flagged        (higher = fewer misses)
  precision    = share of flags that were really distorted              (higher = fewer false alarms)
  false alarms = share of faithful statements that were flagged
per kind of distortion, with every miss and false alarm listed.

Usage (in the project folder; uses GEMINI_API_KEY from .streamlit/secrets.toml or the environment):
    python evaluate.py --papers "../sample_files_for_PR"
    python evaluate.py --papers "../sample_files_for_PR" --checker same        # compare: writer model checks itself
    python evaluate.py --papers "../sample_files_for_PR" --checker gemini-2.5-pro
Results are written to evaluation/results/<date>_<checker>.md and .json.
"""
import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict

import pipeline as pl

HERE = os.path.dirname(os.path.abspath(__file__))


def load_claims(path=os.path.join(HERE, "evaluation", "claims.json")):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def api_key():
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        try:
            import tomllib
            with open(os.path.join(HERE, ".streamlit", "secrets.toml"), "rb") as fh:
                key = str(tomllib.load(fh).get("GEMINI_API_KEY", ""))
        except (OSError, ValueError):
            key = ""
    key = key.strip().strip('"\'')
    return "" if key.startswith("paste-your") else key


BUSY_WAITS = (30, 60)                       # seconds to wait before retrying a paper when Google is busy


def run(llm, papers_dir, claims, skipped=None):
    """Checks every claim against its paper. Returns one row per claim with the checker's verdict.
    Papers that cannot be checked because the AI service stays busy are added to `skipped`."""
    skipped = [] if skipped is None else skipped
    rows = []
    by_paper = defaultdict(list)
    for c in claims:
        by_paper[c["paper"]].append(c)
    for paper, items in by_paper.items():
        path = os.path.join(papers_dir, paper)
        if not os.path.exists(path):
            print(f"  skipped (not found): {paper}")
            continue
        with open(path, "rb") as fh:
            passages, _ = pl.extract_passages(fh.read())
        ids = {f"E{i + 1}": c for i, c in enumerate(items)}
        t0 = time.time()
        items = [{"id": k, "part": "Evaluation", "text": c["text"]} for k, c in ids.items()]
        results = None
        for attempt, wait in enumerate((0, *BUSY_WAITS)):
            if wait:
                print(f"  Google's AI service is busy – waiting {wait} s, then trying {paper[:30]} again …")
                time.sleep(wait)
            try:
                results = pl.verify(llm, passages, items)
                break
            except Exception as e:
                if not any(t in str(e) for t in pl.TRANSIENT) or attempt == len(BUSY_WAITS):
                    print(f"  could not check {paper[:48]}: {str(e)[:120]}")
                    break
        if results is None:
            skipped.append(paper)
            continue
        print(f"  {paper[:48]:48s} {len(items):3d} statements  {time.time() - t0:5.1f}s")
        for r in results:
            c = ids[r["id"]]
            rows.append({**c, "verdict": r["verdict"], "flagged": r["verdict"] in pl.FLAGGED,
                         "explanation": r.get("explanation", ""), "rule_flags": r.get("rule_flags", []),
                         "evidence": [p.pid for p in r.get("evidence", [])]})
    return rows


def metrics(rows):
    wrong = [r for r in rows if r["label"] == "wrong"]
    ok = [r for r in rows if r["label"] == "ok"]
    tp = sum(r["flagged"] for r in wrong)
    fp = sum(r["flagged"] for r in ok)
    per_kind = {}
    for kind in sorted({r["kind"] for r in wrong}):
        ks = [r for r in wrong if r["kind"] == kind]
        per_kind[kind] = {"caught": sum(r["flagged"] for r in ks), "total": len(ks)}
    return {
        "statements": len(rows), "distorted": len(wrong), "faithful": len(ok),
        "recall": tp / len(wrong) if wrong else None,
        "precision": tp / (tp + fp) if (tp + fp) else None,
        "false_alarm_rate": fp / len(ok) if ok else None,
        "accuracy": (tp + len(ok) - fp) / len(rows) if rows else None,
        "caught_as_wrong": sum(r["verdict"] in pl.WRONG for r in wrong),     # not just "needs review"
        "per_kind": per_kind,
        "misses": [r for r in wrong if not r["flagged"]],
        "false_alarms": [r for r in ok if r["flagged"]],
    }


def pct(x):
    return "n/a" if x is None else f"{100 * x:.0f}%"


def report(m, writer, checker, setting, used=None, skipped=None):
    used = used or {}
    skipped = skipped or []
    models = ", ".join(f"{k} ({v} batch{'es' if v > 1 else ''})" for k, v in sorted(used.items(), key=lambda kv: -kv[1]))
    lines = [f"# CiteFlow checker evaluation – {time.strftime('%Y-%m-%d %H:%M')}", "",
             f"Checker setting: {setting}; first-choice checker: **{checker}**; writer model in the app: {writer}", "",
             f"Models that actually checked: {models or checker}"
             + (" – more than one model was used because busy models were skipped" if len(used) > 1 else ""), "",
             *([f"**Incomplete run:** {len(skipped)} paper(s) could not be checked because Google's AI service stayed busy "
                f"({', '.join(skipped)}). Run the evaluation again later for the full result.", ""] if skipped else []),
             f"- Statements: {m['statements']} ({m['distorted']} distorted, {m['faithful']} faithful)",
             f"- **Recall (distortions caught): {pct(m['recall'])}** – {m['distorted'] - len(m['misses'])} of {m['distorted']}"
             f" (of these, {m['caught_as_wrong']} labelled exaggerated/unsupported, the rest 'needs review')",
             f"- **Precision (flags that were real problems): {pct(m['precision'])}**",
             f"- False alarms on faithful statements: {pct(m['false_alarm_rate'])} – {len(m['false_alarms'])} of {m['faithful']}",
             f"- Overall accuracy: {pct(m['accuracy'])}", "", "## By kind of distortion", "",
             "| Kind | Caught |", "|---|---|"]
    lines += [f"| {k.replace('_', ' ')} | {v['caught']} / {v['total']} |" for k, v in m["per_kind"].items()]
    lines += ["", "## Missed distortions", ""]
    lines += [f"- {r['id']} ({r['kind']}): “{r['text']}” → {r['verdict']}" for r in m["misses"]] or ["- none"]
    lines += ["", "## False alarms", ""]
    lines += [f"- {r['id']}: “{r['text']}” → {r['verdict']}: {r['explanation'] or '; '.join(r['rule_flags'])}"
              for r in m["false_alarms"]] or ["- none"]
    return "\n".join(lines) + "\n"


# ============================================================== claim/evidence pair evaluation (claim types)

TYPES = pl.DISTORTIONS
MODES = {
    "ai_only": "AI verdict and the AI's own type labels",
    "ai_levels": "AI ratings compared by code (the app's decision rule)",
    "ai_levels_cues": "AI ratings compared by code + wording cues (needs-review safety net)",
}
FREEZE = os.path.join(HERE, "evaluation", "test_freeze.json")
CACHE = os.path.join(HERE, "evaluation", "cache")
COLUMNS = ("id", "paper", "evidence", "claim", "type")


def _read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)


def sha256(path):
    import hashlib
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _read_table(path):
    """Rows of a CSV (comma or semicolon, any common encoding) or .xlsx file as dicts with lower-case keys."""
    if path.lower().endswith(".xlsx"):
        try:
            import openpyxl
        except ImportError:
            sys.exit("Reading .xlsx needs openpyxl (pip install openpyxl), or save the sheet as 'CSV UTF-8'.")
        ws = openpyxl.load_workbook(path, read_only=True, data_only=True).active
        rows = [["" if c is None else str(c) for c in r] for r in ws.iter_rows(values_only=True)]
    else:
        import csv
        with open(path, "rb") as fh:
            raw = fh.read()
        for enc in ("utf-8-sig", "cp1250", "cp1252"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        first = text.splitlines()[0] if text.strip() else ""
        delim = ";" if first.count(";") > first.count(",") else ","
        rows = list(csv.reader(text.splitlines(), delimiter=delim))
    if not rows:
        return []
    head = [h.strip().lower() for h in rows[0]]
    return [dict(zip(head, [c.strip() for c in r], strict=False)) for r in rows[1:] if any(c.strip() for c in r)]


def load_pairs(path):
    """Claim/evidence pairs. type: 'faithful' or one or more distortion codes separated by ';'.
    Rows whose id starts with EXAMPLE are ignored. Stops with a clear message on any malformed row."""
    rows = _read_table(path)
    if rows and not set(COLUMNS) <= set(rows[0]):
        sys.exit(f"{path}: missing column(s) {', '.join(sorted(set(COLUMNS) - set(rows[0])))}. "
                 f"Expected: id,paper,page,evidence,claim,type,notes")
    pairs, problems, seen = [], [], set()
    for n, r in enumerate(rows, 2):
        pid = r.get("id", "")
        if pid.upper().startswith("EXAMPLE"):
            continue
        raw = [t.strip().lower().replace(" ", "_").replace("-", "_") for t in r.get("type", "").split(";") if t.strip()]
        bad = [t for t in raw if t not in TYPES and t != "faithful"]
        if not pid or not r.get("evidence") or not r.get("claim") or not raw or bad or pid in seen \
                or ("faithful" in raw and len(raw) > 1):
            problems.append(f"line {n} ({pid or 'no id'}): " + (f"unknown type {bad}" if bad else
                            "duplicate id" if pid in seen else "id, evidence, claim and type are all required"))
            continue
        seen.add(pid)
        pairs.append({"id": pid, "paper": r.get("paper", ""), "page": r.get("page", ""), "evidence": r["evidence"],
                      "claim": r["claim"], "gold": [t for t in raw if t != "faithful"], "notes": r.get("notes", "")})
    if problems:
        sys.exit("Please fix these rows first:\n  " + "\n  ".join(problems))
    return pairs


def freeze(path):
    """Records the test file's fingerprint before any test run, so later edits are detected."""
    record = {"file": os.path.basename(path), "sha256": sha256(path), "frozen_at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "pairs": len(load_pairs(path))}
    if os.path.exists(FREEZE):
        old = _read_json(FREEZE)
        if old.get("sha256") == record["sha256"]:
            return old
        sys.exit(f"A different test set was already frozen on {old['frozen_at']} ({old['sha256'][:12]}…). "
                 "The test set must not change after freezing. Delete evaluation/test_freeze.json only if no test run "
                 "has been made yet.")
    os.makedirs(os.path.dirname(FREEZE), exist_ok=True)
    _write_json(FREEZE, record)
    return record


def check_frozen(path):
    if not os.path.exists(FREEZE):
        sys.exit("The test set is not frozen yet. Run once:  python evaluate.py --pairs " + path + " --freeze")
    rec = _read_json(FREEZE)
    if rec["sha256"] != sha256(path):
        sys.exit(f"The test file changed after it was frozen on {rec['frozen_at']}. A test set must not be edited "
                 "after freezing; restore the frozen version.")
    return rec


def _key(*parts):
    import hashlib
    return hashlib.sha256("\x1f".join(map(str, parts)).encode()).hexdigest()[:24]


def check_pair(checker, writer, pair, cues=False, prompt=None):
    """One pair through exactly the app's checking code: verify, then faithful rewrite + re-check if flagged."""
    passages = [pl.Passage(pid="P1-1", page=0, text=pair["evidence"])]
    title = re.sub(r"\s*\(?https?://\S+\)?", "", pair.get("paper", "")).strip(" ,;")
    if title:                                  # the app sees the whole paper; the pair gives at least its title
        passages.insert(0, pl.Passage(pid="P0-1", page=0, text="Paper title: " + title))
    item = [{"id": "C1", "part": "Evaluation", "text": pair["claim"]}]
    t0 = time.time()
    r = pl.verify(checker, passages, item, cues=cues, prompt=prompt)[0]
    if writer is not None and prompt is None:
        pl.rewrite_flagged(writer, checker, passages, [r], cues=cues)
    return {"id": pair["id"], "paper": pair["paper"], "claim": pair["claim"], "evidence": pair["evidence"],
            "gold": pair["gold"], "verdict": r["verdict"], "ai_verdict": r.get("ai_verdict", r["verdict"]),
            "levels": r.get("levels", {}), "distortions": r.get("distortions", []), "ai_types": r.get("ai_types", []),
            "ai_main": r.get("ai_main", ""), "types": r.get("types", []),
            "cue_types": r.get("cue_types", []), "issue_type": r.get("issue_type", ""),
            "explanation": r.get("explanation", ""), "rule_flags": r.get("rule_flags", []),
            "checked_by": r.get("checked_by", ""), "rewrite": r.get("rewrite", "") if r.get("rewrite_rounds") else "",
            "rewrite_verified": r.get("rewrite_verified"), "rewrite_strength_kept": r.get("rewrite_strength_kept"),
            "rewrite_rounds": r.get("rewrite_rounds"), "rewrite_by": r.get("rewrite_by", ""),
            "rewrite_check": r.get("rewrite_check"), "seconds": round(time.time() - t0, 1)}


def run_pairs(checker, writer, pairs, repeat=1, workers=4, cache=True, tag="", prompt=None):
    """Checks every pair; finished pairs are cached so an interrupted run resumes where it stopped.
    Returns (rows, failures)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    os.makedirs(CACHE, exist_ok=True)
    version = _key(pl.VERIFY_PROMPT if prompt is None else prompt, pl.REWRITE_PROMPT, pl.SCALES, pl.CUES)
    rows, failures = {}, []

    def one(p):
        path = os.path.join(CACHE, _key(tag, version, checker.models, getattr(writer, "models", None),
                                        p["id"], p["claim"], p["evidence"], repeat) + ".json")
        if cache and os.path.exists(path):
            return _read_json(path)
        row = check_pair(checker, writer, p, prompt=prompt)
        row["gold"] = p["gold"]
        if cache:
            _write_json(path, row)
        return row

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futures = {ex.submit(one, p): p for p in pairs}
        for f in as_completed(futures):
            p = futures[f]
            try:
                rows[p["id"]] = f.result()
            except Exception as e:
                failures.append((p["id"], f"{type(e).__name__}: {str(e)[:160]}"))
    return [rows[p["id"]] for p in pairs if p["id"] in rows], failures


def decide(row, mode):
    """(flagged, predicted types) for one row under one decision rule."""
    wrong = row["ai_verdict"] in pl.WRONG
    if mode == "baseline":
        return row["verdict"] in pl.FLAGGED, []
    if mode == "ai_only":
        ai = ([row["ai_main"]] if row.get("ai_main") else []) + [t for t in row["ai_types"] if t != row.get("ai_main")]
        return wrong, ai if wrong else []
    types = pl.main_first(row["distortions"], row.get("ai_main", ""), row["ai_types"])
    flagged = wrong or bool(types)
    if mode == "ai_levels_cues" and not flagged and row["cue_types"]:
        return True, list(row["cue_types"])
    return flagged, types


def wilson(k, n, z=1.96):
    """95% Wilson score interval for k successes out of n."""
    if not n:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return max(0.0, c - h), min(1.0, c + h)


def _ratio(k, n):
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "value": k / n if n else None, "ci95": [lo, hi]}


def pair_metrics(rows, mode):
    dec = {r["id"]: decide(r, mode) for r in rows}
    distorted = [r for r in rows if r["gold"]]
    faithful = [r for r in rows if not r["gold"]]
    tp = sum(dec[r["id"]][0] for r in distorted)
    fp = sum(dec[r["id"]][0] for r in faithful)
    out = {"mode": mode, "pairs": len(rows), "distorted": len(distorted), "faithful": len(faithful),
           "detection_recall": _ratio(tp, len(distorted)), "detection_precision": _ratio(tp, tp + fp),
           "false_alarm_rate": _ratio(fp, len(faithful)), "per_type": {}}
    for t in TYPES:                     # each flagged claim is counted under its main (first) type
        gold = {r["id"] for r in rows if t in r["gold"]}
        pred = {r["id"] for r in rows if dec[r["id"]][0] and dec[r["id"]][1][:1] == [t]}
        k = len(gold & pred)
        p, rc = _ratio(k, len(pred)), _ratio(k, len(gold))
        f1 = (2 * p["value"] * rc["value"] / (p["value"] + rc["value"])
              if p["value"] and rc["value"] else (0.0 if gold else None))
        out["per_type"][t] = {"precision": p, "recall": rc, "f1": f1, "tp": k, "fp": len(pred - gold), "fn": len(gold - pred)}
    vals = [v for v in out["per_type"].values() if v["recall"]["n"]]
    out["macro"] = {m: (sum((v[m]["value"] or 0) if m != "f1" else (v["f1"] or 0) for v in vals) / len(vals) if vals else None)
                    for m in ("precision", "recall", "f1")}
    cols = list(TYPES) + ["other", "not flagged"]
    matrix = {g: dict.fromkeys(cols, 0) for g in list(TYPES) + ["faithful"]}
    for r in rows:
        flagged, types = dec[r["id"]]
        g = r["gold"][0] if r["gold"] else "faithful"
        pred = "not flagged" if not flagged else (types[0] if types else "other")
        matrix[g][pred] += 1
    out["confusion"] = matrix
    return out


def rewrite_metrics(rows):
    """Faithful rewrites of correctly flagged distorted claims: re-check pass rate and strength preservation."""
    done = [r for r in rows if r["gold"] and r.get("rewrite_rounds")]
    first = sum(1 for r in done if r["rewrite_verified"] and r["rewrite_rounds"] == 1)
    final = sum(1 for r in done if r["rewrite_verified"])
    kept = [r for r in done if r["rewrite_verified"] and r["rewrite_strength_kept"] is not None]
    return {"rewritten": len(done), "verified_first_try": _ratio(first, len(done)),
            "verified_after_retry": _ratio(final, len(done)),
            "strength_kept": _ratio(sum(1 for r in kept if r["rewrite_strength_kept"]), len(kept))}


def _pct(x):
    return "n/a" if x is None else f"{100 * x:.0f}%"


def _fmt(ratio):
    if ratio["value"] is None:
        return "n/a"
    lo, hi = ratio["ci95"]
    return f"{_pct(ratio['value'])} ({ratio['k']}/{ratio['n']}; 95% CI {_pct(lo)}–{_pct(hi)})"


def _spread(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return "n/a"
    mean = sum(vals) / len(vals)
    return _pct(mean) if len(vals) == 1 else f"{_pct(mean)} (runs: {_pct(min(vals))}–{_pct(max(vals))})"


def stability(runs, mode):
    """Share of pairs that got the same decision (flagged or not, and the main type) in every run."""
    if len(runs) < 2:
        return None
    ids = set.intersection(*[{r["id"] for r in rows} for rows in runs])
    def key(row):
        flagged, types = decide(row, mode)
        return (flagged, types[0] if flagged and types else "")

    same = sum(1 for i in ids if len({key(next(r for r in rows if r["id"] == i)) for rows in runs}) == 1)
    return same / len(ids) if ids else None


def pairs_report(data_path, data_info, results, primary="ai_levels"):
    """results: {checker model: [rows of run 1, rows of run 2, ...]}"""
    L = [f"# CiteFlow claim-type overclaim evaluation – {time.strftime('%Y-%m-%d %H:%M')}", ""]
    L += [f"**Data:** `{os.path.basename(data_path)}` · {data_info['pairs']} pairs · sha256 `{data_info['sha256'][:16]}…`",
          f"**Role of this data:** {data_info['role']}", ""]
    for model, runs in results.items():
        rows = runs[0]
        L += [f"## Checker: `{model}`  ·  writer for rewrites: `{data_info.get('writer', '')}`", "",
              f"{len(runs)} run(s), temperature 0, no model fallback (strict). Pairs: {len(rows)} "
              f"({sum(1 for r in rows if r['gold'])} distorted, {sum(1 for r in rows if not r['gold'])} faithful).", "",
              "### Decision rules compared (same AI output, different decision rule)", "",
              "| Rule | Detection recall | Detection precision | False alarms | Macro F1 (4 types) | Same flag and main type in every run |",
              "|---|---|---|---|---|---|"]
        for mode in MODES:
            ms = [pair_metrics(r, mode) for r in runs]
            stab = stability(runs, mode)
            L.append(f"| {MODES[mode]}{' **(app)**' if mode == primary else ''} | "
                     f"{_spread([m['detection_recall']['value'] for m in ms])} | "
                     f"{_spread([m['detection_precision']['value'] for m in ms])} | "
                     f"{_spread([m['false_alarm_rate']['value'] for m in ms])} | "
                     f"{_spread([m['macro']['f1'] for m in ms])} | {_pct(stab) if stab is not None else 'single run'} |")
        if "baseline" in data_info.get("baseline", {}).get(model, {}):
            b = [pair_metrics(r, "baseline") for r in data_info["baseline"][model]["baseline"]]
            L.append(f"| Previous checker (before claim types), detection only | {_spread([m['detection_recall']['value'] for m in b])} | "
                     f"{_spread([m['detection_precision']['value'] for m in b])} | {_spread([m['false_alarm_rate']['value'] for m in b])} | – | – |")
        m1 = pair_metrics(rows, primary)
        ms = [pair_metrics(r, primary) for r in runs]
        L += ["", f"### Per distortion type – {MODES[primary]}", "",
              "Precision and recall per type, run 1 with 95% Wilson intervals; mean and range over all runs in brackets.", "",
              "| Distortion type | Precision | Recall | F1 | Mean over runs (P / R) |", "|---|---|---|---|---|"]
        for t in TYPES:
            v = m1["per_type"][t]
            L.append(f"| {pl.DISTORTION_LABELS[t]} | {_fmt(v['precision'])} | {_fmt(v['recall'])} | {_pct(v['f1'])} | "
                     f"{_spread([m['per_type'][t]['precision']['value'] for m in ms])} / "
                     f"{_spread([m['per_type'][t]['recall']['value'] for m in ms])} |")
        L += ["", f"- Distorted claims detected (any type): {_fmt(m1['detection_recall'])}",
              f"- Flags that were real distortions: {_fmt(m1['detection_precision'])}",
              f"- False alarms on faithful claims: {_fmt(m1['false_alarm_rate'])}", "",
              "### Confusion matrix (run 1; rows = true type, columns = predicted main type)", "",
              "| True \\ Predicted | " + " | ".join(pl.DISTORTION_LABELS.get(c, c) for c in list(TYPES) + ["other", "not flagged"]) + " |",
              "|---" * (len(TYPES) + 3) + "|"]
        for g, row in m1["confusion"].items():
            L.append(f"| {pl.DISTORTION_LABELS.get(g, g)} | " + " | ".join(str(row[c]) for c in row) + " |")
        rw = rewrite_metrics(rows)
        L += ["", "### Faithful rewrites (run 1)", "",
              f"Correctly flagged distorted claims rewritten by the writer model and re-checked by the checker: {rw['rewritten']}",
              f"- Passed the re-check on the first try: {_fmt(rw['verified_first_try'])}",
              f"- Passed after one retry with the checker's feedback: {_fmt(rw['verified_after_retry'])}",
              f"- Strength preserved: on every overstated scale the verified rewrite is rated exactly at the evidence's level "
              f"(not weaker): {_fmt(rw['strength_kept'])}",
              "", "### Cases (run 1)", ""]
        dec = {r["id"]: decide(r, primary) for r in rows}
        miss = [r for r in rows if r["gold"] and not (dec[r["id"]][0] and dec[r["id"]][1][:1] and dec[r["id"]][1][0] in r["gold"])]
        fa = [r for r in rows if not r["gold"] and dec[r["id"]][0]]
        good = [r for r in rows if r["gold"] and dec[r["id"]][1][:1] and dec[r["id"]][1][0] in r["gold"] and r.get("rewrite_verified")]

        def lv(r):
            return "; ".join(f"{sc}: paper {ev} / claim {cl}" for sc, (ev, cl) in r["levels"].items() if ev != cl) or "levels equal"

        L += ["**Successes (correct type, verified rewrite):**", ""]
        L += [f"- {r['id']} [{', '.join(r['gold'])}] “{r['claim']}” → {lv(r)} → rewrite: “{r['rewrite']}”" for r in good[:6]] or ["- none"]
        L += ["", "**Missed or wrong type:**", ""]
        L += [f"- {r['id']} [true: {', '.join(r['gold'])}; predicted: {', '.join(dec[r['id']][1]) or 'not flagged'}] "
              f"“{r['claim']}” → {lv(r)}" for r in miss] or ["- none"]
        L += ["", "**False alarms on faithful claims:**", ""]
        L += [f"- {r['id']} [predicted: {', '.join(dec[r['id']][1]) or r['ai_verdict']}] “{r['claim']}” → "
              f"{r['explanation'] or lv(r)}" for r in fa] or ["- none"]
        L.append("")
    L += ["## Method", "",
          "- Each pair gives the checker one claim and its evidence passage; the checker runs exactly the app's code "
          "(`pipeline.verify`, then `pipeline.rewrite_flagged`). This measures distortion detection and typing given the "
          "evidence; finding the evidence inside a whole paper is a separate step not measured here.",
          "- The AI rates evidence and claim on four scales (causal strength, scope, certainty, finding vs recommendation); "
          "a claim rated stronger than its evidence is flagged with that distortion type.",
          "- Each flagged claim is counted under its main distortion type (other types it also shows are listed but not "
          "scored). Precision = claims given type X that truly are X; recall = true X claims given type X. A pair "
          "labelled with two types counts as correct if its main type is either.",
          "- The checker sees the paper title and the evidence passage (the app sees the whole paper).",
          "- 95% confidence intervals: Wilson score interval. Runs repeat the same API calls to show run-to-run variation.",
          "- Strict mode: one fixed checker model, no fallback; a failed call stops the run (rerun resumes from cache)."]
    return "\n".join(L) + "\n"


def main_pairs(a, key):
    data_path = a.pairs
    if a.freeze:
        rec = freeze(data_path)
        print(f"Test set frozen: {rec['file']} · {rec['pairs']} pairs · sha256 {rec['sha256'][:16]}… · {rec['frozen_at']}")
        return rec
    pairs = load_pairs(data_path)
    if a.test:
        if len(a.checker) != 1:
            sys.exit("Test mode uses exactly one checker model, chosen beforehand on the development set.")
        rec = check_frozen(data_path)
        role = (f"HELD-OUT TEST SET, frozen {rec['frozen_at']} before any test run; written by a person from papers not "
                f"used in development; not used to build or tune the checker.")
    else:
        role = "Development set: used to build, tune and choose the checker. Not a held-out result."
    chain = pl.model_chain(key)
    writer_model = a.writer or chain[0]
    info = {"pairs": len(pairs), "sha256": sha256(data_path), "role": role, "writer": writer_model, "baseline": {}}
    results, usage, failed_all = {}, Counter(), []

    def record(e):
        if e.get("ok"):
            usage[(e["model"], "calls")] += 1
            usage[(e["model"], "prompt_tokens")] += e.get("prompt_tokens", 0)
            usage[(e["model"], "output_tokens")] += e.get("output_tokens", 0) + e.get("thinking_tokens", 0)

    for model in a.checker:
        checker = pl.LLM(key, model, fallbacks=[], on_call=record)            # strict: no fallback
        writer = pl.LLM(key, writer_model, fallbacks=[], on_call=record)
        runs = []
        for rep in range(1, a.repeats + 1):
            print(f"Checking {len(pairs)} pairs with {model} (run {rep}/{a.repeats}) …")
            rows, failures = run_pairs(checker, writer, pairs, rep, a.workers, not a.no_cache, tag="pairs")
            if failures:
                failed_all += [(model, rep, *f) for f in failures]
                continue
            runs.append(rows)
        if a.baseline and not failed_all:
            base = pl.LLM(key, model, fallbacks=[], on_call=record)
            brows, bfail = run_pairs(base, None, pairs, 1, a.workers, not a.no_cache, tag="baseline", prompt=pl.VERIFY_PROMPT_V1)
            if not bfail:
                info["baseline"][model] = {"baseline": [brows]}
        if runs:
            results[model] = runs
    if failed_all:
        print("\nThe run is INCOMPLETE – some calls failed even after retries (finished pairs are saved):")
        for model, rep, pid, err in failed_all[:12]:
            print(f"  {model} run {rep} {pid}: {err}")
        sys.exit("No report written. Run the same command again to resume where it stopped.")
    text = pairs_report(data_path, info, results)
    text += "\n## API usage (calls made in this sitting; answers reused from the cache are not counted)\n\n" + "\n".join(
        f"- {m}: {usage[(m, 'calls')]} calls, {usage[(m, 'prompt_tokens')]:,} input tokens, {usage[(m, 'output_tokens')]:,} output tokens"
        for m in sorted({m for m, _ in usage})) + "\n"
    os.makedirs(a.out, exist_ok=True)
    stem = os.path.join(a.out, time.strftime("%Y%m%d-%H%M") + ("_TEST_" if a.test else "_dev_") + "_".join(a.checker))
    with open(stem + ".md", "w", encoding="utf-8") as fh:
        fh.write(text)
    summary = {m: {mode: [pair_metrics(r, mode) for r in runs] for mode in MODES} for m, runs in results.items()}
    with open(stem + ".json", "w", encoding="utf-8") as fh:
        json.dump({"data": os.path.basename(data_path), **{k: v for k, v in info.items() if k != "baseline"},
                   "test_mode": a.test, "metrics": summary, "rows": results}, fh, ensure_ascii=False, indent=1)
    print("\n" + text)
    print(f"Saved: {stem}.md")
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--papers", default=os.path.join(HERE, "..", "sample_files_for_PR"), help="folder with the sample PDFs")
    ap.add_argument("--checker", dest="checker_list", nargs="+", default=None,
                    help="checker model(s); with --pairs several can be compared on the development set")
    ap.add_argument("--claims", default=os.path.join(HERE, "evaluation", "claims.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "evaluation", "results"))
    ap.add_argument("--pairs", help="claim/evidence pairs (.csv or .xlsx): evaluates the claim-type checker")
    ap.add_argument("--test", action="store_true", help="held-out test run: one checker, frozen data only")
    ap.add_argument("--freeze", action="store_true", help="record the test file's fingerprint before any test run")
    ap.add_argument("--repeats", type=int, default=1, help="repeat the whole run to show run-to-run variation")
    ap.add_argument("--workers", type=int, default=4, help="pairs checked in parallel")
    ap.add_argument("--writer", default="", help="writer model for faithful rewrites (default: the app's first choice)")
    ap.add_argument("--baseline", action="store_true", help="also run the previous checker prompt for comparison")
    ap.add_argument("--no-cache", action="store_true", help="ignore saved answers from earlier runs")
    a = ap.parse_args(argv)
    if a.pairs:
        a.checker = a.checker_list or ["auto"]
    key = "" if a.pairs and a.freeze else api_key()
    if a.pairs:
        if not a.freeze and not key:
            sys.exit("No GEMINI_API_KEY found (.streamlit/secrets.toml or environment).")
        if not a.freeze and a.checker == ["auto"]:
            sys.exit("Name the checker model(s), e.g.  --checker gemini-3.1-pro-preview gemini-3.8-flash")
        return main_pairs(a, key)
    a.checker = (a.checker_list or ["auto"])[0]
    if not key:
        sys.exit("No GEMINI_API_KEY found (.streamlit/secrets.toml or environment).")
    chain = pl.model_chain(key)
    checks = pl.checker_chain(chain, a.checker)
    used, failed = Counter(), Counter()

    def record(event):                      # which models really answered (busy ones fall back to others)
        (used if event["ok"] else failed)[event["model"]] += 1

    llm = pl.LLM(key, checks[0], fallbacks=checks[1:], on_call=record)
    data = load_claims(a.claims)
    print(f"Checking {len(data['claims'])} statements with {checks[0]} …")
    skipped = []
    rows = run(llm, a.papers, data["claims"], skipped)
    if not rows:
        sys.exit("No statements could be checked: " + ("Google's AI service is busy, please try again in a few minutes."
                                                      if skipped else f"no papers found in {a.papers}"))
    m = metrics(rows)
    text = report(m, chain[0], checks[0], a.checker, dict(used), skipped)
    if failed:
        text += "\nBusy or failed attempts (retried automatically): " + ", ".join(f"{k} ×{v}" for k, v in failed.items()) + "\n"
    os.makedirs(a.out, exist_ok=True)
    stem = os.path.join(a.out, time.strftime("%Y%m%d-%H%M") + "_" + a.checker.replace("/", "-"))
    with open(stem + ".md", "w", encoding="utf-8") as fh:
        fh.write(text)
    with open(stem + ".json", "w", encoding="utf-8") as fh:
        json.dump({"checker": checks[0], "models_used": dict(used), "failed_attempts": dict(failed), "skipped_papers": skipped,
                   "setting": a.checker, "writer": chain[0], "rows": rows,
                   **{k: v for k, v in m.items() if k not in ("misses", "false_alarms")}}, fh, ensure_ascii=False, indent=1)
    print("\n" + text)
    print(f"Saved: {stem}.md")
    return m


if __name__ == "__main__":
    main()
