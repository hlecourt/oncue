"""State, report, backups and atomic writes under the guard's state directory."""

import hashlib
import json
import os
from datetime import datetime

from .outcome import SETTLED_DECISIONS


STATE_DIRECTORY = os.environ.get("SUBTITLE_GUARD_STATE", "/config/subtitle-guard")
BACKUP_DIRECTORY = os.environ.get("SUBTITLE_GUARD_BACKUPS", "/data/backup/subtitle-guard")


def report(subtitle, provider, outcome):
    before, after = outcome.get("before") or {}, outcome.get("after") or {}
    row = (now(), outcome["decision"], outcome.get("reference", ""), before.get("matched", ""), after.get("matched", ""), before.get("noise", ""),
           outcome.get("null_objective", ""), outcome.get("segments", ""), ",".join(str(minute) for minute in outcome.get("weak_minutes", [])),
           outcome.get("matches", "") or outcome.get("reason", ""), provider, subtitle)
    os.makedirs(STATE_DIRECTORY, exist_ok=True)
    with open(os.path.join(STATE_DIRECTORY, "report.tsv"), "a") as handle:
        handle.write("\t".join(str(value) for value in row) + "\n")


def print_needs_human():
    latest = {}
    for line in open(os.path.join(STATE_DIRECTORY, "report.tsv")):
        fields = line.rstrip("\n").split("\t")
        latest[fields[-1]] = fields
    for subtitle, fields in sorted(latest.items()):
        if not os.path.exists(subtitle):
            continue
        weak, note = (fields[8], fields[9]) if len(fields) >= 12 else ("", "")
        if fields[1] in SETTLED_DECISIONS and not weak:
            continue
        print(f"{fields[1]:13s} {fields[0]}  weak minutes {weak or '-'}  {note}  {subtitle}")
    return 0


def backup_path(subtitle):
    subtitle = os.path.abspath(subtitle)
    folder = os.path.join(BACKUP_DIRECTORY, os.path.basename(os.path.dirname(os.path.dirname(subtitle))),
                          os.path.basename(subtitle), datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(folder, exist_ok=True)
    return folder


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


def now():
    return datetime.now().isoformat(timespec="seconds")
