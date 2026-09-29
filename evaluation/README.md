# Checker evaluation

`claims.json` holds 48 labelled statements about four DEIK.AI Challenge sample papers: 23 faithful and 25 distorted
in ten different ways (changed or rounded numbers, causal or certainty overclaims, overgeneralisation, wrong
attribution, simulation presented as real, removed limitations, contradictions and invented facts).

Run it (the sample PDFs are not in the repository, point `--papers` to your copy):

```
python evaluate.py --papers "../sample_files_for_PR"                  # independent checker (the app default)
python evaluate.py --papers "../sample_files_for_PR" --checker same   # writer model checks itself, for comparison
```

Each run saves a Markdown report and the raw verdicts in `results/`. Metrics:

- **Recall**: share of distorted statements that were flagged (missed problems lower it).
- **Precision**: share of flags that were real problems (false alarms lower it).
- **False alarm rate**: share of faithful statements that were flagged.

Re-run the evaluation before changing prompts or models, and keep the reports as evidence.
