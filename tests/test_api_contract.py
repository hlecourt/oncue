# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path

from oncue.api.contract import SCHEMA_PATH, schema_text

ROOT = Path(__file__).resolve().parent.parent


def test_the_committed_openapi_schema_matches_the_code():
    assert (ROOT / SCHEMA_PATH).read_text() == schema_text(), "run: python -m oncue.api.contract"
