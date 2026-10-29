# SPDX-License-Identifier: GPL-3.0-or-later
"""Every change to a subtitle file goes through here: one writer at a time, no symlinks, atomic replace, owner and mode kept."""

import errno
import fcntl
import hashlib
import os
import shutil
import stat
import tempfile
import time
from contextlib import ExitStack, contextmanager

from .errors import SubtitleTooLargeError, UnsafePathError
from .logs import LEVEL, log
from .storage import backup_folders, backup_path, state_path

MAX_SUBTITLE_BYTES = 20 * 1024 * 1024
ROOTS = [os.path.realpath(root) for root in os.environ.get("SUBTITLE_GUARD_ROOTS", os.environ.get("SUBTITLE_GUARD_LIBRARY", "")).split(os.pathsep) if root]
TEMPORARY_SUFFIX = ".subtitle-guard-tmp"
KEEP_VERSIONS = int(os.environ.get("SUBTITLE_GUARD_KEEP_VERSIONS", "5"))
KEEP_DAYS = int(os.environ.get("SUBTITLE_GUARD_KEEP_DAYS", "30"))


def inside_roots(path):
    return not ROOTS or any(os.path.realpath(path).startswith(root + os.sep) for root in ROOTS)


@contextmanager
def subtitle_lock(path):
    lock_file = state_path("locks", hashlib.sha1(os.path.realpath(path).encode()).hexdigest() + ".lock")
    os.makedirs(os.path.dirname(lock_file), exist_ok=True)
    with open(lock_file, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


@contextmanager
def subtitle_locks(*paths):
    with ExitStack() as stack:
        for path in sorted({os.path.realpath(path) for path in paths}):
            stack.enter_context(subtitle_lock(path))
        yield


def read_regular(path, confined=True):
    if confined and not inside_roots(path):
        raise UnsafePathError(f"refusing a file outside SUBTITLE_GUARD_ROOTS: {path}")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise UnsafePathError(f"refusing to read a symlink: {path}") from error
        raise
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise UnsafePathError(f"refusing to read something that is not a regular file: {path}")
        data = handle.read(MAX_SUBTITLE_BYTES + 1)
    if len(data) > MAX_SUBTITLE_BYTES:
        raise SubtitleTooLargeError(f"larger than {MAX_SUBTITLE_BYTES // (1024 * 1024)} MB: {path}")
    return data


def replace_file(path, data):
    if not inside_roots(path):
        raise UnsafePathError(f"refusing to write outside SUBTITLE_GUARD_ROOTS: {path}")
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


def file_hash(path):
    return hashlib.sha1(read_regular(path)).hexdigest()


def save_backup(subtitle, label):
    folder = backup_path(subtitle)
    save_version(subtitle, folder, label)
    prune_backups(subtitle)
    return folder


def prune_backups(subtitle):
    folders = backup_folders(subtitle)
    oldest_kept = time.time() - KEEP_DAYS * 86400
    kept = {folders[0], *folders[-KEEP_VERSIONS:]} if folders else set()
    for folder in folders:
        if folder not in kept and os.path.getmtime(folder) < oldest_kept:
            shutil.rmtree(folder)
            log("backup.pruned", f"old backup removed: {folder}", LEVEL["debug"], subtitle=subtitle, folder=folder)


def save_version(subtitle, folder, label):
    destination = os.path.join(folder, label + os.path.splitext(subtitle)[1])
    with open(destination, "xb") as handle:
        handle.write(read_regular(subtitle))
    return destination


def restore_version(subtitle, source):
    replace_file(subtitle, read_regular(source, confined=False))
