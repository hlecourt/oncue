# SPDX-License-Identifier: GPL-3.0-or-later
"""Every change to a subtitle file goes through here: one writer at a time, no symlinks, atomic replace, owner and mode kept."""

import fcntl
import hashlib
import os
import stat
import tempfile
from contextlib import contextmanager

from .errors import SubtitleTooLargeError, UnsafePathError
from .logs import LEVEL, log
from .storage import state_path

MAX_SUBTITLE_BYTES = 20 * 1024 * 1024
TEMPORARY_SUFFIX = ".subtitle-guard-tmp"


@contextmanager
def subtitle_lock(path):
    lock_file = state_path("locks", hashlib.sha1(os.path.abspath(path).encode()).hexdigest() + ".lock")
    os.makedirs(os.path.dirname(lock_file), exist_ok=True)
    with open(lock_file, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def read_regular(path):
    if os.path.islink(path):
        raise UnsafePathError(f"refusing to read a symlink: {path}")
    with open(path, "rb") as handle:
        data = handle.read(MAX_SUBTITLE_BYTES + 1)
    if len(data) > MAX_SUBTITLE_BYTES:
        raise SubtitleTooLargeError(f"larger than {MAX_SUBTITLE_BYTES // (1024 * 1024)} MB: {path}")
    return data


def replace_file(path, data):
    if os.path.islink(path):
        raise UnsafePathError(f"refusing to write through a symlink: {path}")
    previous = os.stat(path) if os.path.exists(path) else None
    descriptor, temporary = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", suffix=TEMPORARY_SUFFIX, dir=os.path.dirname(os.path.abspath(path)))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if previous:
            keep_owner_and_mode(temporary, previous, path)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.remove(temporary)
        raise


def keep_owner_and_mode(temporary, previous, path):
    os.chmod(temporary, stat.S_IMODE(previous.st_mode))
    created = os.stat(temporary)
    if (created.st_uid, created.st_gid) == (previous.st_uid, previous.st_gid):
        return
    try:
        os.chown(temporary, previous.st_uid, previous.st_gid)
    except PermissionError as error:
        log("file.owner-not-kept", f"could not keep owner {previous.st_uid}:{previous.st_gid}: {error}", LEVEL["warning"], subtitle=path)


def save_version(subtitle, folder, label):
    destination = os.path.join(folder, label + os.path.splitext(subtitle)[1])
    with open(destination, "xb") as handle:
        handle.write(read_regular(subtitle))
    return destination


def restore_version(subtitle, source):
    replace_file(subtitle, read_regular(source))
