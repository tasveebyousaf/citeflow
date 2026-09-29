"""Interface language (English / Magyar). tr("English text") returns the Hungarian version when Magyar is chosen.

Only what is displayed is translated; internal values (page names, options sent to the AI) stay in English.
The Hungarian texts live in ui/hu.py. Content language (the press release etc.) is chosen separately in the Studio.
"""
import streamlit as st

from ui.hu import HU

LANGS = {"en": "English", "hu": "Magyar"}


def ui_lang():
    ss = st.session_state
    if "ui_lang" not in ss:
        try:
            locale = (st.context.locale or "").lower()
        except Exception:
            locale = ""
        ss.ui_lang = "hu" if locale.startswith("hu") else "en"
    return ss.ui_lang


def tr(text):
    return HU.get(text, text) if ui_lang() == "hu" else text


def language_picker(key):
    """A small English / Magyar switch. It always shows the current language and changes it only when clicked."""
    st.session_state[key] = ui_lang()

    def changed():
        choice = st.session_state.get(key)
        if choice:                                   # clicking the selected option again clears it: keep the language
            st.session_state.ui_lang = choice

    st.segmented_control("Language / Nyelv", list(LANGS), format_func=LANGS.get, key=key, on_change=changed,
                         label_visibility="collapsed")


def choose(widget, label, options, **kwargs):
    """A select box or radio that shows translated options but returns the English value."""
    shown = [tr(o) for o in options]
    picked = widget(label, shown, **kwargs)
    return options[shown.index(picked)] if picked in shown else picked
