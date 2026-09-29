"""Hungarian interface: every visible text is translated, translations keep placeholders and markup,
and the app works end to end in Magyar."""
import ast
import glob
import os
import re

from test_app import click, new_app, run, seed_project

import store
from ui.hu import HU

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAME_IN_BOTH = {"LinkedIn", "Facebook", "Instagram", "X", "YouTube", "Admin"}


def ui_texts():
    texts = set()
    for f in glob.glob(os.path.join(ROOT, "ui", "*.py")):
        with open(f, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "tr" \
                    and n.args and isinstance(n.args[0], ast.Constant):
                texts.add(n.args[0].value)
    return texts


def has_words(s):
    return bool(re.search(r"[A-Za-z]{2,}", re.sub(r"<[^>]+>|\{[^}]*\}|&nbsp;", " ", s)))


def test_every_interface_text_is_translated():
    missing = [t for t in ui_texts() if t not in HU and t not in SAME_IN_BOTH and has_words(t)]
    assert not missing, f"Add Hungarian for: {missing[:5]}"


def test_translations_keep_placeholders_and_markup():
    for en, hu in HU.items():
        assert sorted(re.findall(r"\{[^{}]*\}", en)) == sorted(re.findall(r"\{[^{}]*\}", hu)), en
        assert sorted(re.findall(r"<[^>]+>", en)) == sorted(re.findall(r"<[^>]+>", hu)), en
        assert en.count("**") == hu.count("**"), en


def test_app_works_in_hungarian(app_path, pdf_bytes):
    store.create_user("anna@unideb.hu", "Debrecen2026", "Anna")
    at = new_app(app_path)
    at.session_state["ui_lang"] = "hu"
    run(at)
    assert "Bejelentkezés" in [b.label for b in at.button]
    at.text_input[0].input("anna@unideb.hu")
    at.text_input[1].input("Debrecen2026")
    click(at, "Bejelentkezés")
    assert at.session_state["user"] == "anna@unideb.hu"
    assert {"Stúdió", "Projektek", "Fiók"} <= {b.label for b in at.button}
    seed_project(at, pdf_bytes)
    assert any(b.label.startswith("Összes javítása") for b in at.button)
    click(at, [b.label for b in at.button if b.label.startswith("Összes javítása")][0])
    assert "The system is designed to assist experts." in at.session_state["release"]
    click(at, "Rövidebben, ütősebben")
    assert any("újraellenőrizve" in m["content"] for m in at.session_state["chat"])
    at.session_state["page"] = "Account"
    run(at)
    assert any("Mit tárol a CiteFlow" in m.value for m in at.markdown)


def test_default_language_is_english(app_path):
    at = new_app(app_path)
    assert at.session_state["ui_lang"] == "en" and "Sign in" in [b.label for b in at.button]


def test_language_switch_by_click(app_path):
    at = new_app(app_path)
    at.get("button_group")[0].set_value("hu")
    run(at)
    assert at.session_state["ui_lang"] == "hu" and "Bejelentkezés" in [b.label for b in at.button]
    at.get("button_group")[0].set_value("en")
    run(at)
    assert at.session_state["ui_lang"] == "en" and "Sign in" in [b.label for b in at.button]
