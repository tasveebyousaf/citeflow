# CiteFlow

**Turn research into trusted PR content, with every claim traceable, every piece human-reviewed, and every channel ready to publish.**

CiteFlow turns a research paper into a press release, social posts for four platforms and a narrated video, and then checks every sentence against the source before anything is published.

Built for the DEIK.AI Challenge 2026, category 2.C (AI-Assisted PR Content Generation), University of Debrecen.

## Why

Exaggeration in science news usually starts in the press release itself. In a BMJ study of university press releases, 40% contained exaggerated advice, 33% exaggerated causal claims and 36% exaggerated inference from animals to humans. When the press release exaggerated, news stories were far more likely to repeat it ([Sumner et al., 2014](https://pmc.ncbi.nlm.nih.gov/articles/PMC4262123)).

Generative AI makes writing faster, but it can also make overclaiming faster. CiteFlow adds the missing step: verification.

## How it works

1. **Read.** The PDF is split into numbered source passages (page + paragraph).
2. **Draft.** An LLM writes a press release, posts for four platforms and a 5–6 scene video script from the source only. It never invents quotes and leaves a placeholder for researcher-approved quotes instead.
3. **Verify.** A separate fact-checking prompt reviews every sentence and labels it *supported*, *exaggerated*, *unsupported* or *not a claim*, with the source passages it relies on and a faithful rewrite for flagged sentences.
4. **See the proof.** Pick any sentence and CiteFlow shows the actual page of the paper with the supporting passage highlighted and the claim's numbers outlined.
5. **Rule check.** Deterministic rules double-check the AI. A "supported" claim must cite a real passage, and every number in it must appear in that passage. If not, it becomes *needs review*.
6. **Video.** Only verified narration is spoken; flagged lines are replaced by their faithful rewrite. The video combines stock footage (Pexels, labelled as illustrative), the paper's own figures, word-by-word captions synced to the voice-over (edge-tts) and smooth transitions, in 16:9 or 9:16.
7. **Social.** Separate posts for LinkedIn, Facebook, Instagram and X, each checked, with image cards built from the paper's figures and one-click share or copy.
8. **Stress test.** The app can write a deliberately hyped version and check it, to show the verifier catches exaggeration.
9. **Human approval.** Nothing is exported until a person confirms they reviewed the flagged claims. Then a package (release, post, video, subtitles, verification report) can be downloaded.

10. **Refine with feedback.** Tell CiteFlow in plain words what to change ("warmer tone", "shorter LinkedIn post"); it rewrites the content and checks every claim again.
11. **Insights.** A publishing plan per project: best days and times per platform, hashtags, format tips and a launch-week schedule (AI guidance based on typical engagement patterns, not live platform analytics).
12. **Workspace.** Sign in or create an account, connect your channels (LinkedIn, Facebook, Instagram, X, blog, YouTube) and reopen past projects. One click on *Publish on …* copies the post, saves the image and opens the platform; on X and LinkedIn the text is pre-filled.

13. **Decide on every flag.** A "needs your decision" panel lists every flagged sentence across the release, posts, image text and video, with one-click *Use faithful version*, *Keep as is* or *Remove* (plus *Fix all* / *Keep all remaining*). Changes are re-checked automatically.
14. **Designed posts.** Every post gets a designed image (headline, key number, three fact boxes, call to action) in 16:9, 1:1 and 4:5, a swipeable carousel for Instagram and LinkedIn, and an animated MP4 version. The facts printed on images are fact-checked too.

Public content never mentions verification: the fact-check is only visible to the team inside CiteFlow.

Principle: **AI drafts and verifies. A human approves and publishes.**

The app has sign-in and sign-up, a **Studio** (create and review), **Projects** (all saved work), **Insights** (publishing plan), **Channels** (save your LinkedIn, Facebook, Instagram, X, blog and YouTube accounts so posts are addressed and opened in the right place) and an **Account** page.

## Security and privacy

- Passwords: PBKDF2-SHA256 with 600,000 iterations and a per-user salt; policy of 8+ characters with a letter and a number.
- Sign-in lockout: 5 failed attempts per account (20 per network) lock sign-in for 10 minutes.
- Usage limits per user, for the shared demo account and for all users together protect the AI quota (configurable under `[limits]` in secrets).
- Uploads: 25 MB limit, PDF validation (signature, not encrypted, max 80 pages).
- All dynamic text is HTML-escaped; every prompt treats the paper as data, not instructions (prompt-injection guard).
- Keys only in Streamlit secrets; `secrets.toml` and `data/` are never committed. Team passwords can be stored as hashes: `python hash_password.py`.
- Privacy note with consent at sign-up; users can delete their account and all data on the Account page.
- Dependencies pinned; Dependabot checks for security updates weekly.

Full documentation: [`docs/CiteFlow_Documentation.pdf`](docs/CiteFlow_Documentation.pdf).

## Database (PostgreSQL)

CiteFlow stores accounts, projects, uploaded papers and videos in **PostgreSQL** when `DATABASE_URL` is set in secrets, and in the local `data/` folder otherwise. Both backends offer the same functions (`store.py` chooses; `store_pg.py` is the PostgreSQL version), so the rest of the app is unchanged. Tables are created automatically on first start.

- Free option: a [Neon](https://neon.com) project (choose a Europe region if offered), then paste its connection string as `DATABASE_URL`.
- Move existing local data into the database: `python migrate_to_postgres.py` (safe to run more than once).

## Run it locally

Requirements: Python 3.10+, internet connection, a free Gemini API key ([Google AI Studio](https://aistudio.google.com/apikey)) and optionally a free Pexels API key ([pexels.com/api](https://www.pexels.com/api/)) for stock footage.

1. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`, paste your keys and set the team accounts under `[users]`.
2. Install and start:

```bash
pip install -r requirements.txt
streamlit run app.py
```

Accounts: people can create an account on the sign-in page. Optional team accounts can be added to `secrets.toml` under `[users]` (`"name@unideb.hu" = "password"`); a demo account (`demo@citeflow.app` / `citeflow2026`) is available when no team accounts are set. Accounts and projects are stored in PostgreSQL when `DATABASE_URL` is set, otherwise in the `data/` folder (which resets on Streamlit Community Cloud when the app restarts).

Users never see or enter keys. The newest available Gemini Flash model is selected automatically, with fallback to other models if one is busy.

## Deploy (Streamlit Community Cloud, free)

1. Push this repository to GitHub (without `secrets.toml`).
2. On [share.streamlit.io](https://share.streamlit.io), create an app from the repository, main file `app.py`.
3. In the app's **Settings → Secrets**, paste the contents of your `secrets.toml`.

## Files

| File | What it does |
|---|---|
| `app.py` | Starts the app and routes between pages |
| `ui/core.py` | Configuration, secrets, usage limits, AI clients (writer and independent checker), monitoring, look, navigation |
| `ui/project.py` | The open project: checking, decisions on flagged sentences, images, saving and reopening |
| `ui/results.py` | Studio results: release, posts, images, video, fact check, feedback, stress test, export |
| `ui/login.py`, `ui/page_*.py` | Sign-in and one module per page (Studio, Projects, Insights, Channels, Account, Admin) |
| `ui/i18n.py`, `ui/hu.py` | English / Magyar interface and the Hungarian texts |
| `pipeline.py` | PDF passages (with OCR for scanned pages), generation, verification, rule checks, model selection |
| `cards.py` | Designed social images, carousels and animated posts |
| `video.py` | Explainer video (stock footage, paper figures, word-synced captions, voice-over) |
| `store.py`, `store_pg.py` | Storage: JSON files or PostgreSQL (accounts, projects, limits, lockout, monitoring events, retention) |
| `evaluate.py`, `evaluation/` | Accuracy evaluation of the fact-checker on 48 labelled statements |
| `tests/` | Automated tests (run with `pytest`), using a stand-in AI model |
| `hash_password.py`, `migrate_to_postgres.py` | Helpers for team passwords and moving local data into PostgreSQL |
| `docs/CiteFlow_Documentation.pdf` | Full technical and product documentation |

## Quality: tests, evaluation and monitoring

- **Tests:** `pip install -r requirements-dev.txt`, then `pytest` (52 tests; set `TEST_DATABASE_URL` to also test PostgreSQL). GitHub runs the tests and the `ruff` code checks on every upload.
- **Checker accuracy:** `python evaluate.py --papers "../sample_files_for_PR"` measures recall, precision and false alarms on 48 labelled statements (see `evaluation/README.md`). The fact-checker uses a different model than the writer by default (`CHECKER_MODEL`).
- **Monitoring:** every AI call (model, tokens, time, errors) and every app error is recorded for 90 days. Admins listed in `ADMINS` see usage, costs and errors on the Admin page. Optional crash reporting with `SENTRY_DSN`.
- **Scanned PDFs:** pages without a text layer are read with Tesseract OCR (English and Hungarian), up to 30 pages.
- **Languages:** the interface is available in English and Magyar (Hungarian texts: please have changes reviewed by a native speaker).
- **Data retention:** projects not changed for 365 days (14 for the demo account) are deleted automatically.
- **Accessibility:** WCAG AA text contrast, visible keyboard focus, verdicts shown by line pattern as well as colour, screen-reader labels for flagged sentences.

## Limitations

- The verifier is an LLM and can make mistakes. It is a second pair of eyes, not a replacement for the researchers' approval.
- Checks are against the uploaded document only, not the wider literature.
- Video visuals are stock footage and paper figures, not footage of the actual research. Stock clips are labelled as illustrative.
- Direct posting is not automated: platforms require app review, and a human should approve before publishing.
- Very long documents are truncated to about 150,000 characters of source text.

## Data

The sample materials of the DEIK.AI Challenge are not included in this repository.
