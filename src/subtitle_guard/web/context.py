# SPDX-License-Identifier: GPL-3.0-or-later
"""State shared by one request: authentication, the CSRF token, one-shot notices and a per-request cache."""

import secrets
import threading
from collections import OrderedDict

MAX_NOTICES = 200
AUTH = {"required": False}
NOTICES = OrderedDict()
REQUEST = threading.local()
DIAGNOSES = threading.BoundedSemaphore(2)


def notice(message):
    key = secrets.token_urlsafe(9)
    NOTICES[key] = message
    while len(NOTICES) > MAX_NOTICES:
        NOTICES.popitem(last=False)
    return key


def cached(name, function):
    store = getattr(REQUEST, "cache", None)
    if store is None:
        return function()
    if name not in store:
        store[name] = function()
    return store[name]
