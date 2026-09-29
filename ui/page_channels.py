"""Channels page."""

import streamlit as st

from ui.core import CHANNELS, save_channels, ss
from ui.i18n import tr


def page_channels():
    st.markdown(tr("<div class='cf-h2'>Channels</div><div class='cf-lead'>Connect the accounts your team publishes to. "
                "CiteFlow uses them to address each post and open the right place to publish.</div>"), unsafe_allow_html=True)
    data = ss.setdefault("channels", {})
    cols = st.columns(2, gap="medium")
    for k, (key, label, ico, color, field, ph) in enumerate(CHANNELS):
        with cols[k % 2]:
            with st.container(border=True):
                on = bool(data.get(key))
                st.markdown(f"<div class='cf-chan'><div class='cf-chan-ico' style='background:{color}'>{ico}</div>"
                            f"<div style='flex:1'><b>{tr(label)}</b><br><span style='color:var(--muted);font-size:13px'>{tr(field)}</span></div>"
                            f"<span class='cf-status {'cf-on' if on else 'cf-off'}'>{tr('CONNECTED') if on else tr('NOT CONNECTED')}</span></div>",
                            unsafe_allow_html=True)
                val = st.text_input(tr(field), value=data.get(key, ""), placeholder=ph, key=f"ch_{key}",
                                    label_visibility="collapsed")
                b1, b2 = st.columns(2)
                if b1.button(tr("Save"), key=f"save_{key}", width="stretch", type="primary"):
                    data[key] = val.strip()
                    save_channels(ss.user, data)
                    st.rerun()
                if on and b2.button(tr("Disconnect"), key=f"dis_{key}", width="stretch"):
                    data[key] = ""
                    save_channels(ss.user, data)
                    st.rerun()
    st.caption(tr("Publishing mode: CiteFlow opens each platform with the approved post and image ready, so a person "
               "publishes it. Fully automatic posting needs each platform's app review (Meta, LinkedIn, X) and is on the roadmap."))
