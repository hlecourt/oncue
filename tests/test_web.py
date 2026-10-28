import json
import shutil
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from subtitle_guard import jobs, storage, web
from subtitle_guard.subtitles import parse_subtitle
from media import video_with_reference, write_subtitle_file


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *arguments):
        return None


@pytest.fixture
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def get(url, headers=None):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {})) as response:
            return response.status, response.read().decode(), response.headers
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode(), error.headers


def post(url, form, headers=None):
    opener = urllib.request.build_opener(NoRedirect)
    request = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), headers=headers or {}, method="POST")
    try:
        opener.open(request)
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Location", "")
    raise AssertionError("expected a redirect")


def test_dashboard_jobs_and_api(server):
    job_id = jobs.enqueue(jobs.KIND["check"], {"subtitle": "/media/a.fr.srt"})
    jobs.claim(1)
    jobs.fail(job_id, "OSError: gone")
    status, page, _ = get(server + "/")
    assert status == 200 and "Overview" in page and "OSError: gone" in page and "need a look" in page
    assert json.loads(get(server + "/api/status")[1])["status"] == {"failed": 1}
    code, location = post(server + "/", {"action": "retry-job", "job": str(job_id)})
    assert code == 303 and "run+again" in location and jobs.job(job_id)["status"] == "queued"
    assert get(server + "/health")[1] == "ok"
    assert get(server + "/nowhere")[0] == 404


def test_unknown_subtitles_are_never_touched(server, tmp_path):
    stranger = tmp_path / "stranger.srt"
    stranger.write_text("1\n00:00:01,000 --> 00:00:02,000\nx\n")
    code, location = post(server + "/", {"action": "revert", "path": str(stranger)})
    assert "Unknown+subtitle" in location
    assert get(server + "/subtitle?" + urllib.parse.urlencode({"path": str(stranger)}))[0] == 404


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg and ffprobe")
def test_review_page_and_manual_shift(server, tmp_path):
    video, starts, ends = video_with_reference(tmp_path)
    subtitle = video.with_suffix(".fr.srt")
    write_subtitle_file(subtitle, starts, ends, lambda value: value + 3 if 120 <= value < 180 else value)
    storage.report(str(subtitle), "scan", {"decision": "kept", "weak_minutes": [2], "confidence": 70, "confidence_band": "medium"})
    status, page, _ = get(server + "/review")
    assert status == 200 and "Movie.2020.fr.srt" in page
    status, page, _ = get(server + "/subtitle?" + urllib.parse.urlencode({"path": str(subtitle), "minute": 2}))
    assert status == 200 and "line " in page and "ligne " in page and "s too late<div class=\"muted\">shows at" in page and "on time" in page
    assert "1 part of this subtitle is out of sync." in page and "What is wrong (1 part)" in page and "s too late</b>" in page and "Fix this part" in page
    assert "Fix everything" in page and "Try another subtitle" not in page
    code, location = post(server + "/", {"action": "shift", "path": str(subtitle), "minute": "2", "first": "2:03", "last": "3:03", "offset": "-3"})
    assert code == 303 and "Done. The lines from 2:03 to 3:03 now appear 3.0 s earlier." in urllib.parse.unquote_plus(location)
    shifted = [start for start, _, _ in parse_subtitle(str(subtitle))["cues"] if 120 <= start < 180]
    expected = [start for start in starts if 120 <= start < 180]
    assert max(abs(a - b) for a, b in zip(shifted, expected)) < 0.01


def test_token_protects_the_ui(server, monkeypatch):
    monkeypatch.setattr(web, "TOKEN", "secret")
    storage.report("/media/Private.Name.fr.srt", "scan", {"decision": "rejected"})
    status, page, _ = get(server + "/")
    assert status == 401 and "Needs a look" not in page and "Private.Name" not in page
    assert get(server + "/health")[0] == 200
    opener = urllib.request.build_opener(NoRedirect)
    try:
        opener.open(server + "/?token=secret")
    except urllib.error.HTTPError as error:
        cookie = error.headers.get("Set-Cookie")
    assert cookie.startswith("subtitle_guard_token=secret") and "HttpOnly" in cookie
    assert get(server + "/", {"Cookie": "subtitle_guard_token=secret"})[0] == 200
    assert post(server + "/", {"action": "retry-job", "job": "1"})[0] == 401



@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg and ffprobe")
def test_fix_this_part_and_fix_everything(server, tmp_path):
    video, starts, ends = video_with_reference(tmp_path)
    subtitle = video.with_suffix(".fr.srt")
    write_subtitle_file(subtitle, starts, ends, lambda value: value + 3 if 120 <= value < 180 else value)
    storage.report(str(subtitle), "scan", {"decision": "fixed", "weak_minutes": [2], "confidence": 70, "confidence_band": "medium"})
    part = review_part(str(video), str(subtitle))
    code, location = post(server + "/", {"action": "fix-part", "path": str(subtitle), "minute": "2", "first": f"{part['first']:.3f}",
                                         "last": f"{part['last']:.3f}", "offset": f"{part['shift']:.2f}"})
    assert code == 303 and "Done." in urllib.parse.unquote_plus(location)
    moved = [start for start, _, _ in parse_subtitle(str(subtitle))["cues"] if 119 <= start < 181]
    assert max(abs(a - b) for a, b in zip(moved, [start for start in starts if 120 <= start < 180])) < 0.25
    write_subtitle_file(subtitle, starts, ends, lambda value: value * 23.976 / 25 + 6)
    code, location = post(server + "/", {"action": "fix-all", "path": str(subtitle), "minute": "0"})
    assert "The whole subtitle is back in sync." in urllib.parse.unquote_plus(location)
    status, page, _ = get(server + "/subtitle?" + urllib.parse.urlencode({"path": str(subtitle)}))
    assert "This subtitle is in sync with the video." in page


def test_the_log_page_is_events_with_copyable_metadata_and_a_search(server):
    from subtitle_guard import logs
    logs.log("bazarr.hook-received", "post-processing hook lang=fr provider=opensubtitles id=1 mode=inline", subtitle="/media/Show/Show.S01E01.fr.srt")
    logs.log("job.failed", "job 9 failed: OSError: gone", logs.LEVEL["error"], job=9, trace="Traceback: boom")
    logs.log("reference.extracted", "2 candidate tracks extracted in 0.40s", logs.LEVEL["debug"], tracks=[{"index": 3, "lines": 512}])
    status, page, _ = get(server + "/log")
    assert status == 200 and "<b class=\"event\">bazarr.hook-received</b>" in page and "job 9 failed: OSError: gone" in page
    assert "ERROR" in page and "DEBUG" in page and "Traceback: boom" in page and "Show.S01E01.fr.srt" in page
    assert '<button class="small copy" type="button">Copy</button><pre>{' in page and "&quot;lines&quot;: 512" in page
    page = get(server + "/log?q=job+9")[1]
    assert "job 9 failed" in page and "bazarr.hook-received" not in page
    assert [event["job"] for event in json.loads(get(server + "/api/log?q=job+9")[1])] == [9]
    assert '"event": "reference.extracted"' in get(server + "/log.jsonl")[1]


def review_part(video, subtitle):
    from subtitle_guard import review
    return review.diagnose(video, subtitle)["parts"][0]



@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg and ffprobe")
def test_the_review_list_offers_the_obvious_fix(server, tmp_path):
    video, starts, ends = video_with_reference(tmp_path)
    subtitle = video.with_suffix(".fr.srt")
    write_subtitle_file(subtitle, starts, ends, lambda value: value + 3 if 120 <= value < 180 else value)
    storage.report(str(subtitle), "manual", {"decision": "manual", "weak_minutes": [2], "confidence": 55, "confidence_band": "low"})
    get(server + "/subtitle?" + urllib.parse.urlencode({"path": str(subtitle)}))
    status, page, _ = get(server + "/review")
    assert "Fix it</button>" in page
    code, location = post(server + "/", {"action": "fix-all", "path": str(subtitle), "back": "review"})
    assert location.startswith("/review?") and "back in sync" in urllib.parse.unquote_plus(location)
    storage.report(str(subtitle), "hook", {"decision": "wrong-episode", "matches": "S01E03", "confidence": 0, "confidence_band": "none"})
    assert "Move it to S01E03</button>" in get(server + "/review")[1]



@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg and ffprobe")
def test_the_history_shows_what_each_fix_moved(server, tmp_path):
    from subtitle_guard.guard import evaluate_and_fix
    video, starts, ends = video_with_reference(tmp_path)
    shifted = video.with_suffix(".fr.srt")
    write_subtitle_file(shifted, starts, ends, lambda value: value + 3 if 120 <= value < 180 else value)
    storage.report(str(shifted), "opensubtitles", evaluate_and_fix(str(video), str(shifted), True))
    speed = video.with_suffix(".en.srt")
    write_subtitle_file(speed, starts, ends, lambda value: value * 23.976 / 25)
    storage.report(str(speed), "scan", evaluate_and_fix(str(video), str(speed), False))
    status, page, _ = get(server + "/subtitle?" + urllib.parse.urlencode({"path": str(speed)}))
    assert "This subtitle runs at the wrong speed." in page and "Fix this part" not in page
    storage.report(str(speed), "scan", evaluate_and_fix(str(video), str(speed), True))
    page = get(server + "/history")[1]
    assert "Started by: Bazarr, from opensubtitles" in page and "Started by: Library scan" in page
    assert "lines from 2:" in page and "s earlier</div>" in page and "Speed corrected: it was made for a faster version of the video (4.3%)" in page
    assert "/ 100, high" in page



@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg and ffprobe")
def test_a_fixed_subtitle_shows_what_was_changed_before_and_after(server, tmp_path):
    from subtitle_guard.guard import evaluate_and_fix
    video, starts, ends = video_with_reference(tmp_path)
    subtitle = video.with_suffix(".fr.srt")
    write_subtitle_file(subtitle, starts, ends, lambda value: value + 3 if 120 <= value < 180 else value)
    storage.report(str(subtitle), "opensubtitles", evaluate_and_fix(str(video), str(subtitle), True))
    page = get(server + "/subtitle?" + urllib.parse.urlencode({"path": str(subtitle), "minute": 2}))[1]
    assert "This subtitle is in sync with the video." in page and "What was changed is shown below." in page
    assert "What was changed" in page and "started by Bazarr, from opensubtitles, confidence" in page and "s earlier</div>" in page
    assert "fixed by the guard, now on time" in page and 'class="fixed' in page
    assert "moved 2.9 s earlier</div>" in page or "moved 3.0 s earlier</div>" in page
