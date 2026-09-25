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

## Run it locally

Requirements: Python 3.10+, internet connection, a free Gemini API key ([Google AI Studio](https://aistudio.google.com/apikey)) and optionally a free Pexels API key ([pexels.com/api](https://www.pexels.com/api/)) for stock footage.

1. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`, paste your keys and set the team accounts under `[users]`.
2. Install and start:

```bash
pip install -r requirements.txt
streamlit run app.py
```

Accounts: people can create an account on the sign-in page. Optional team accounts can be added to `secrets.toml` under `[users]` (`"name@unideb.hu" = "password"`); a demo account (`demo@citeflow.app` / `citeflow2026`) is available when no team accounts are set. Accounts and projects are stored in the `data/` folder (on Streamlit Community Cloud this storage resets when the app restarts).

Users never see or enter keys. The newest available Gemini Flash model is selected automatically, with fallback to other models if one is busy.

## Deploy (Streamlit Community Cloud, free)

1. Push this repository to GitHub (without `secrets.toml`).
2. On [share.streamlit.io](https://share.streamlit.io), create an app from the repository, main file `app.py`.
3. In the app's **Settings → Secrets**, paste the contents of your `secrets.toml`.

## Files

| File | What it does |
|---|---|
| `app.py` | Streamlit interface |
| `pipeline.py` | PDF passages, generation, verification, rule checks |
| `store.py` | Accounts, connected channels and saved projects |
| `cards.py` | Designed social images, carousels and animated posts |
| `video.py` | Explainer video (stock footage, paper figures, word-synced captions, voice-over) and social image cards |

## Limitations

- The verifier is an LLM and can make mistakes. It is a second pair of eyes, not a replacement for the researchers' approval.
- Checks are against the uploaded document only, not the wider literature.
- Video visuals are stock footage and paper figures, not footage of the actual research. Stock clips are labelled as illustrative.
- Direct posting is not automated: platforms require app review, and a human should approve before publishing.
- Very long documents are truncated to about 150,000 characters of source text.

## Data

The sample materials of the DEIK.AI Challenge are not included in this repository.
