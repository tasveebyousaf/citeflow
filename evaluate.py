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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--papers", default=os.path.join(HERE, "..", "sample_files_for_PR"), help="folder with the sample PDFs")
    ap.add_argument("--checker", default="auto", help="auto (independent model), same, or a model name")
    ap.add_argument("--claims", default=os.path.join(HERE, "evaluation", "claims.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "evaluation", "results"))
    a = ap.parse_args(argv)
    key = api_key()
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
