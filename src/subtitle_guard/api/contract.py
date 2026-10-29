# SPDX-License-Identifier: GPL-3.0-or-later
"""Write the OpenAPI schema the front's types are generated from: python -m subtitle_guard.api.contract [PATH]."""

import json
import sys

from .app import create_app

SCHEMA_PATH = "frontend/openapi.json"


def schema_text():
    return json.dumps(create_app("127.0.0.1").openapi(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    with open(sys.argv[1] if len(sys.argv) > 1 else SCHEMA_PATH, "w") as handle:
        handle.write(schema_text())
