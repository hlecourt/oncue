# SPDX-License-Identifier: GPL-3.0-or-later
"""State, report, backups and atomic writes under the guard's state directory."""

import fcntl
import glob
import hashlib
import json
import os
import tempfile
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


def read_json(path):
    with open(path) as handle:
        return json.load(handle)


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", dir=os.path.dirname(path))
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(data, handle)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.remove(temporary)
        raise


@contextmanager
def heavy_disk_read():
    os.makedirs(STATE_DIRECTORY, exist_ok=True)
    with open(os.path.join(STATE_DIRECTORY, "heavy-disk-read.lock"), "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def now():
    return datetime.now().isoformat(timespec="seconds")
