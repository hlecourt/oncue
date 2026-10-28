"""State, report, backups and atomic writes under the guard's state directory."""

import fcntl
import glob
import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime

STATE_DIRECTORY = os.environ.get("SUBTITLE_GUARD_STATE", "/config/subtitle-guard")
BACKUP_DIRECTORY = os.environ.get("SUBTITLE_GUARD_BACKUPS", "/data/backup/subtitle-guard")


def state_path(*parts):
    return os.path.join(STATE_DIRECTORY, *parts)


def file_signature(path):
    status = os.stat(path)
    return f"{status.st_size}:{status.st_mtime_ns}"


def backup_path(subtitle):
    folder = os.path.join(backup_root(subtitle), datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    os.makedirs(folder)
    return folder


def backup_root(subtitle):
    subtitle = os.path.abspath(subtitle)
    identity = hashlib.sha1(subtitle.encode()).hexdigest()[:12]
    return os.path.join(BACKUP_DIRECTORY, os.path.basename(os.path.dirname(os.path.dirname(subtitle))), f"{os.path.basename(subtitle)}.{identity}")


def backup_folders(subtitle):
    subtitle = os.path.abspath(subtitle)
    legacy_root = os.path.join(BACKUP_DIRECTORY, os.path.basename(os.path.dirname(os.path.dirname(subtitle))), os.path.basename(subtitle))
    return sorted(folder for root in (backup_root(subtitle), legacy_root) for folder in glob.glob(os.path.join(glob.escape(root), "*"))
                  if os.path.isdir(folder))


def revert_source(subtitle):
    extension = os.path.splitext(subtitle)[1]
    folders = backup_folders(subtitle)
    originals = [os.path.join(folder, "original" + extension) for folder in reversed(folders)]
    manual = [os.path.join(folder, "before-manual" + extension) for folder in folders]
    return next((path for path in originals + manual if os.path.exists(path)), None)


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
