"""Admin page (usage, costs and errors)."""

import time

import streamlit as st

import store
from ui.core import SENTRY, is_admin, pricing, secret
from ui.i18n import tr


def cost_of(e, prices):
    p = prices.get(e.get("model", ""))
    if not p:
        return None
    return (e.get("prompt_tokens", 0) * float(p.get("input", 0))
            + (e.get("output_tokens", 0) + e.get("thinking_tokens", 0)) * float(p.get("output", 0))) / 1e6


def compact(n):
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}k" if n >= 1e3 else str(n)


def page_admin():
    import pandas as pd
    if not is_admin():
        st.error(tr("This page is for administrators."))
        return
    st.markdown(tr("<div class='cf-h2'>Admin · usage and health</div>"
                "<div class='cf-lead'>AI calls, tokens, costs and errors recorded by CiteFlow (kept for 90 days).</div>"),
                unsafe_allow_html=True)
    days = st.segmented_control(tr("Period"), [1, 7, 30, 90], default=7, format_func=lambda d: tr("{0} days").format(d) if d > 1 else tr("1 day"))
    ev = store.events_since(days or 7)
    calls = [e for e in ev if e.get("kind") == "ai_call"]
    errors = [e for e in ev if not e.get("ok")]
    prices = pricing()
    costs = [c for c in (cost_of(e, prices) for e in calls) if c is not None]
    tokens_in = sum(e.get("prompt_tokens", 0) for e in calls)
    tokens_out = sum(e.get("output_tokens", 0) + e.get("thinking_tokens", 0) for e in calls)
    failed = sum(1 for e in calls if not e.get("ok"))
    k = st.columns(6)
    k[0].metric(tr("AI calls"), len(calls))
    k[1].metric(tr("Failed calls"), f"{failed} ({100 * failed / len(calls):.0f}%)" if calls else "0")
    k[2].metric(tr("Tokens in"), compact(tokens_in))
    k[3].metric(tr("Tokens out"), compact(tokens_out), help=tr("Includes the model's reasoning tokens."))
    k[4].metric(tr("Estimated cost"), f"${sum(costs):,.2f}" if costs else "—",
                help=tr("Set prices per 1M tokens under [pricing] in secrets to see costs."))
    k[5].metric(tr("Active users"), len({e.get("user") for e in ev if e.get("user")}))
    if not ev:
        st.info(tr("No activity recorded in this period yet."))
        return
    df = pd.DataFrame(calls or [{"at": time.time(), "ok": True}])
    df["day"] = pd.to_datetime(df["at"], unit="s").dt.strftime("%m-%d")
    df["result"] = df["ok"].map({True: "succeeded", False: "failed"})
    st.markdown(tr("**AI calls per day**"))
    per_day = df.groupby(["day", "result"]).size().unstack(fill_value=0)
    for col in ("failed", "succeeded"):
        if col not in per_day:
            per_day[col] = 0
    st.bar_chart(per_day[["succeeded", "failed"]], height=220, stack=True, color=["#4f8e78", "#c0563d"])
    c1, c2 = st.columns(2, gap="medium")
    with c1:
        st.markdown(tr("**By model**"))
        if calls:
            m = pd.DataFrame(calls)
            m["tokens"] = m["prompt_tokens"] + m["output_tokens"] + m["thinking_tokens"]
            t = m.groupby("model").agg(calls=("ok", "size"), failed=("ok", lambda x: int((~x.astype(bool)).sum())),
                                       avg_seconds=("latency_ms", lambda x: round(x.mean() / 1000, 1)),
                                       tokens=("tokens", "sum")).reset_index()
            st.dataframe(t, hide_index=True, width="stretch")
    with c2:
        st.markdown(tr("**By user**"))
        if calls:
            u = pd.DataFrame(calls)
            u["tokens"] = u["prompt_tokens"] + u["output_tokens"] + u["thinking_tokens"]
            t = u.groupby("user").agg(calls=("ok", "size"), tokens=("tokens", "sum")).reset_index()
            st.dataframe(t.sort_values("calls", ascending=False).head(15), hide_index=True, width="stretch")
    st.markdown(tr("**Recent errors**"))
    if errors:
        rows = [{"time": time.strftime("%Y-%m-%d %H:%M", time.localtime(e["at"])), "user": e.get("user", ""),
                 "where": e.get("action") or e.get("model"), "error": e.get("detail", "")[:160]} for e in sorted(errors, key=lambda e: e["at"], reverse=True)[:20]]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.caption(tr("No errors in this period."))
    st.caption(tr('Storage: {0} · Error tracking: {1} · Checker model setting: {2}').format(store.backend(), 'Sentry on' if SENTRY else 'off (set SENTRY_DSN to enable)', secret('CHECKER_MODEL') or 'auto'))
