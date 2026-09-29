"""A deterministic stand-in for the Gemini client, so the whole app can be tested without an API key or quota.

It returns fixed content about the synthetic test paper (see conftest.make_pdf) and judges sentences with simple
rules: hype words -> EXAGGERATED/UNSUPPORTED, calls to action -> NOT_A_CLAIM, everything else -> SUPPORTED with the
best-matching passage as evidence. Importing this module replaces pipeline.LLM.
"""
import re

import pipeline as pl

STORY = dict(
    headline="A second pair of eyes for cervical cancer screening",
    subheadline="Debrecen researchers built a system that helps experts check Pap smears.",
    institution="University of Debrecen, Faculty of Informatics",
    card_title="AI that helps screen Pap smears",
    press_release=(
        "Cytology experts examine thousands of cells in every smear by eye.\n\n"
        "The team built a screening system designed to assist cytology experts. "
        "It reached an average accuracy of 88.8% on 339 smears.\n\n"
        "[Quote from the researchers to be added after approval]\n\n"
        "The system proves that AI can replace experts."),
    visual=pl.Visual(kicker="New research", stat_value="88.8%", stat_label="average accuracy",
                     key_points=["Locates and classifies cells", "Designed to assist experts", "Tested on 339 smears"],
                     cta="Read the study"),
)
SOCIAL = dict(
    posts=pl.Posts(linkedin="The screening system is designed to assist cytology experts.\n\nWhat do you think? #AI #Health #Research",
                   facebook="Could AI help experts screen Pap smears? #AI #Health",
                   instagram="A second pair of eyes for cytology experts. #AI #science #health #research",
                   x="A screening system designed to assist cytology experts. #AI"),
    video_title="A second pair of eyes",
    scenes=[pl.Scene(on_screen_text="Thousands of cells", narration="Cytology experts examine thousands of cells in every smear by eye.",
                     wants_figure=False, stock_query="microscope laboratory"),
            pl.Scene(on_screen_text="A system that helps", narration="The screening system is designed to assist cytology experts.",
                     wants_figure=False, stock_query="doctor laboratory")],
)
HYPE = "This breakthrough AI cures cancer.\n\nIt proves that AI can replace experts everywhere."

WRONG_WORDS = ("replace", "cures", "breakthrough")


def _passages(prompt):
    return dict(re.findall(r"^\[(P\d+-\d+)\] (.*)$", prompt, re.M))


def _items(prompt):
    block = prompt.split("ITEMS TO CHECK:", 1)[-1].split("SOURCE DOCUMENT", 1)[0]
    return re.findall(r"^\[([A-Z]\d+)\] (.*)$", block, re.M)


def _best_passage(text, passages):
    words = set(re.findall(r"\w+", text.lower()))
    scored = sorted(passages.items(), key=lambda kv: -len(words & set(re.findall(r"\w+", kv[1].lower()))))
    return scored[0][0] if scored else ""


def _verdict(text, passages):
    low = text.lower()
    if any(w in low for w in WRONG_WORDS):
        kind = "UNSUPPORTED" if "cures" in low else "EXAGGERATED"
        return pl.Verdict(verdict=kind, evidence_ids=[_best_passage(text, passages)], issue_type="certainty inflation",
                          explanation="The paper says the system is designed to assist experts.",
                          suggested_rewrite="The system is designed to assist experts.", item_id="")
    if text.startswith("[Quote") or "?" in text or text.strip().startswith("#") or text.lower().startswith("read the"):
        return pl.Verdict(verdict="NOT_A_CLAIM", evidence_ids=[], issue_type="", explanation="", suggested_rewrite="", item_id="")
    return pl.Verdict(verdict="SUPPORTED", evidence_ids=[_best_passage(text, passages)], issue_type="", explanation="",
                      suggested_rewrite="", item_id="")


class FakeLLM:
    calls: list = []                       # shared log: (schema name, model) for assertions

    fail_next = 0                          # set >0 to make the next calls raise (tests error logging)

    def __init__(self, api_key="", model="fake-model", fallbacks=None, on_call=None, **kw):
        self.model = model
        self.models = [model] + list(fallbacks or [])
        self.log = []
        self.on_call = on_call

    def json_call(self, prompt, schema, temperature=0.2, fast=True):
        FakeLLM.calls.append((schema.__name__, self.model))
        ok = FakeLLM.fail_next <= 0
        if self.on_call:
            self.on_call({"model": self.model, "action": schema.__name__, "ok": ok, "latency_ms": 5,
                          "detail": "" if ok else "RuntimeError: simulated failure",
                          "prompt_tokens": len(prompt) // 4, "output_tokens": 100, "thinking_tokens": 0})
        if not ok:
            FakeLLM.fail_next -= 1
            raise RuntimeError("simulated failure")
        if schema is pl.PartStory:
            return pl.PartStory(**{k: STORY[k] for k in pl.PartStory.model_fields})
        if schema is pl.PartSocial:
            return pl.PartSocial(**SOCIAL)
        if schema is pl.Content:
            c = pl.Content(**STORY, **SOCIAL)
            if "FEEDBACK" in prompt:          # revise: shorter LinkedIn post
                c = c.model_copy(update={"posts": c.posts.model_copy(update={"linkedin": "Shorter LinkedIn post. #AI"})})
            return c
        if schema is pl.Report:
            ps = _passages(prompt)
            out = []
            for iid, text in _items(prompt):
                v = _verdict(text, ps)
                out.append(v.model_copy(update={"item_id": iid}))
            return pl.Report(items=out)
        if schema is pl.Hyped:
            return pl.Hyped(press_release=HYPE)
        if schema is pl.PublishPlan:
            P = pl.PlatformPlan
            return pl.PublishPlan(
                audience_summary="Health-aware adults and clinicians.",
                platforms=[P(platform=n, best_days=["Tuesday", "Thursday"], best_times=["08:00-10:00"],
                             hashtags=["#AI", "#Health", "#UniDeb", "#Research", "#Screening"],
                             format_tip="Carousel", why="Reason.") for n in ("LinkedIn", "Facebook", "Instagram", "X")],
                schedule=[pl.PlanStep(day="Tuesday", time="09:00", platform="LinkedIn", action="Post the summary")],
                trend_angles=["AI in healthcare"], avoid=["Implying diagnosis"])
        raise AssertionError(f"FakeLLM: unexpected schema {schema}")


pl.LLM = FakeLLM
pl.list_flash_models = lambda key: ["fake-model", "fake-model-2"]
