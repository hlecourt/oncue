# SPDX-License-Identifier: GPL-3.0-or-later
"""The language of each request, from its Accept-Language header, and the translation of the words the API sends to the front."""

import json
import os
from contextvars import ContextVar
from functools import cache

LOCALES = ("en", "fr", "zh")
CATALOG_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales")
current_locale = ContextVar("locale", default="en")


def locale_from(header):
    for part in (header or "").split(","):
        language = part.split(";")[0].strip().lower()[:2]
        if language in LOCALES:
            return language
    return "en"


def tr(text, **values):
    template = catalog(current_locale.get()).get(text, text)
    return template.format(**values) if values else template


@cache
def catalog(locale):
    path = os.path.join(CATALOG_FOLDER, f"{locale}.json")
    if locale == "en" or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)
