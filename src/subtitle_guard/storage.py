"""State, report, backups and atomic writes under the guard's state directory."""

import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime

from .outcome import SETTLED_DECISIONS


STATE_DIRECTORY = os.environ.get("SUBTITLE_GUARD_STATE", "/config/subtitle-guard")
BACKUP_DIRECTORY = os.environ.get("SUBTITLE_GUARD_BACKUPS", "/data/backup/subtitle-guard")


def state_path(*parts):
    return os.path.join(STATE_DIRECTORY, *parts)


def report(subtitle, provider, outcome):
    before, after = outcome.get("before") or {}, outcome.get("after") or {}
    row = (now(), outcome["decision"], outcome.get("reference", ""), before.get("matched", ""), after.get("matched", ""), before.get("noise", ""),
           outcome.get("null_objective", ""), outcome.get("segments", ""), ",".join(str(minute) for minute in outcome.get("weak_minutes", [])),
           outcome.get("matches", "") or outcome.get("reason", ""), f"{outcome.get('confidence', '')}/{outcome.get('confidence_band', '')}", provider, subtitle)
    os.makedirs(STATE_DIRECTORY, exist_ok=True)
    with open(os.path.join(STATE_DIRECTORY, "report.tsv"), "a") as handle:
        handle.write("\t".join(str(value) for value in row) + "\n")


def print_needs_human():
    for row in latest_reports():
        if needs_review(row):
            print(f"{row['decision']:13s} {row['time']}  confidence {row['confidence'] or '-'} {row['band']}  weak minutes {row['weak_minutes'] or '-'}  "
                  f"{row['note']}  {row['subtitle']}")
    return 0


def latest_reports():
    path = state_path("report.tsv")
    if not os.path.exists(path):
        return []
    latest = {}
    for line in open(path):
        fields = line.rstrip("\n").split("\t")
        latest[fields[-1]] = fields
    rows = []
    for subtitle, fields in sorted(latest.items()):
        if not os.path.exists(subtitle):
            continue
        score, band = fields[10].split("/") if len(fields) >= 13 else ("", "")
        rows.append({"time": fields[0], "decision": fields[1], "reference": fields[2], "matched": fields[4], "weak_minutes": fields[8] if len(fields) >= 12 else "",
                     "note": fields[9] if len(fields) >= 12 else "", "confidence": score, "band": band, "provider": fields[-2], "subtitle": subtitle})
    return rows


def needs_review(row):
    return row["decision"] not in SETTLED_DECISIONS or bool(row["weak_minutes"]) or row["band"] == "low"


def file_signature(path):
    status = os.stat(path)
    return f"{status.st_size}:{int(status.st_mtime)}"


def backup_path(subtitle):
    folder = os.path.join(backup_root(subtitle), datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(folder, exist_ok=True)
    return folder


def backup_root(subtitle):
    subtitle = os.path.abspath(subtitle)
    return os.path.join(BACKUP_DIRECTORY, os.path.basename(os.path.dirname(os.path.dirname(subtitle))), os.path.basename(subtitle))


def load_state(video, subtitle):
    path = state_file(video, subtitle)
    if not os.path.exists(path):
        return {}
    try:
        return json.load(open(path))
    except ValueError:
        return {}


def save_state(video, subtitle, state):
    write_json(state_file(video, subtitle), state)


def state_file(video, subtitle):
    stem, name = os.path.splitext(os.path.basename(video))[0], os.path.basename(subtitle)
    suffix = name[len(stem):] if name.startswith(stem) else name
    return os.path.join(STATE_DIRECTORY, "state", hashlib.sha1(f"{video}|{suffix}".encode()).hexdigest() + ".json")


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = f"{path}.{os.getpid()}.tmp"
    with open(temporary, "w") as handle:
        json.dump(data, handle)
    os.replace(temporary, path)


@contextmanager
def heavy_disk_read():
    os.makedirs(STATE_DIRECTORY, exist_ok=True)
    with open(os.path.join(STATE_DIRECTORY, "heavy-disk-read.lock"), "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def now():
    return datetime.now().isoformat(timespec="seconds")
