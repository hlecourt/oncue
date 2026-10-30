# SPDX-License-Identifier: GPL-3.0-or-later
import sys

import pytest

from oncue import cli, jobs

HOOK = ["/v.mkv", "/v.fr.srt", "fr", "subdl", "41", "7", "2"]


@pytest.mark.parametrize("extra", [["stale-hash"], ["stale-hash", "queued", "more"]])
def test_a_hook_with_more_than_its_seven_fields_is_a_usage_error(monkeypatch, extra):
    monkeypatch.setattr(sys, "argv", ["oncue", "hook", "--queue", *HOOK, *extra])
    assert cli.main() == 2 and jobs.recent() == []


def test_a_trailing_workers_option_without_a_number_is_a_usage_error(monkeypatch, capsys):
    with pytest.raises(cli.UsageError):
        cli.parse_options(["--workers"])
    monkeypatch.setattr(sys, "argv", ["oncue", "hook", "--queue", *HOOK[:6], "--workers"])
    assert cli.main() == 2 and jobs.recent() == [] and "--workers needs a number" in capsys.readouterr().err
