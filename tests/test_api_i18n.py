# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import string

import pytest

from subtitle_guard.api import i18n

LOCALES = [locale for locale in i18n.LOCALES if locale != "en"]


def placeholders(text):
    return sorted(name for _, name, _, _ in string.Formatter().parse(text) if name is not None)


@pytest.mark.parametrize("locale", LOCALES)
def test_every_translation_keeps_the_placeholders_of_its_english_text(locale):
    with open(os.path.join(i18n.CATALOG_FOLDER, f"{locale}.json"), encoding="utf-8") as handle:
        catalog = json.load(handle)
    for source, translated in catalog.items():
        assert placeholders(translated) == placeholders(source), source


def test_every_language_translates_the_same_texts():
    catalogs = [set(i18n.catalog(locale)) for locale in LOCALES]
    assert all(keys == catalogs[0] for keys in catalogs)


def test_an_unknown_language_or_text_stays_in_english():
    assert i18n.locale_from("de-DE,de;q=0.9") == "en" and i18n.locale_from("") == "en" and i18n.locale_from("pt, zh-TW;q=0.5") == "zh"
    token = i18n.current_locale.set("fr")
    try:
        assert i18n.tr("Nothing moved") == "Rien n'a bougé"
        assert i18n.tr("A text nobody translated {count}", count=2) == "A text nobody translated 2"
    finally:
        i18n.current_locale.reset(token)
