# SPDX-License-Identifier: GPL-3.0-or-later
"""Every change to a subtitle file goes through here: one writer at a time, no symlinks, atomic replace, owner and mode kept."""

import errno
import fcntl
import hashlib
import os
import secrets
import shutil
import stat
import time
from contextlib import ExitStack, contextmanager, suppress

from . import configuration
from .errors import SubtitleTooLargeError, UnsafePathError
from .logs import LEVEL, log
from .storage import backup_folders, backup_path, state_path

MAX_SUBTITLE_BYTES = 20 * 1024 * 1024
ROOTS = [os.path.realpath(root) for root in os.environ.get("SUBTITLE_GUARD_ROOTS", os.environ.get("SUBTITLE_GUARD_LIBRARY", "")).split(os.pathsep) if root]
TEMPORARY_SUFFIX = ".subtitle-guard-tmp"


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
    folder, name = confined(path)
    with open_folder(folder) as folder_descriptor:
        previous = regular_or_none(name, folder_descriptor, path)
        temporary = f".{name}.{secrets.token_hex(4)}{TEMPORARY_SUFFIX}"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder_descriptor)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                if previous:
                    keep_owner_and_mode(handle.fileno(), previous, path)
                os.fsync(handle.fileno())
            os.replace(temporary, name, src_dir_fd=folder_descriptor, dst_dir_fd=folder_descriptor)
        except BaseException:
            with suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=folder_descriptor)
            raise


def remove_file(path):
    folder, name = confined(path)
    with open_folder(folder) as folder_descriptor:
        if regular_or_none(name, folder_descriptor, path) is None:
            raise FileNotFoundError(errno.ENOENT, "no such subtitle", path)
        os.unlink(name, dir_fd=folder_descriptor)


def confined(path):
    absolute = os.path.abspath(path)
    folder, name = os.path.realpath(os.path.dirname(absolute)), os.path.basename(absolute)
    if not inside_roots(os.path.join(folder, name)):
        raise UnsafePathError(f"refusing to change a file outside SUBTITLE_GUARD_ROOTS: {path}")
    return folder, name


@contextmanager
def open_folder(folder):
    descriptor = os.open(os.sep, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in [part for part in folder.split(os.sep) if part]:
            try:
                inner = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            except OSError as error:
                if error.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise UnsafePathError(f"refusing a folder that is or became a link: {folder}") from error
                raise
            os.close(descriptor)
            descriptor = inner
        yield descriptor
    finally:
        os.close(descriptor)


def regular_or_none(name, folder_descriptor, path):
    try:
        existing = os.stat(name, dir_fd=folder_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(existing.st_mode):
        raise UnsafePathError(f"refusing to change something that is not a regular file: {path}")
    return existing


def keep_owner_and_mode(descriptor, previous, path):
    os.fchmod(descriptor, stat.S_IMODE(previous.st_mode))
    created = os.fstat(descriptor)
    if (created.st_uid, created.st_gid) == (previous.st_uid, previous.st_gid):
        return
    try:
        os.fchown(descriptor, previous.st_uid, previous.st_gid)
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
    oldest_kept = time.time() - configuration.value("keep_days") * 86400
    kept = {folders[0], *folders[-configuration.value("keep_versions"):]} if folders else set()
    for folder in folders:
        if folder not in kept and os.path.getmtime(folder) < oldest_kept:
            shutil.rmtree(folder)
            log("backup.pruned", f"old backup removed: {folder}", LEVEL["debug"], subtitle=subtitle, folder=folder)


def clean_up_backups(subtitles):
    removed = 0
    oldest_kept = time.time() - configuration.value("keep_days") * 86400
    for subtitle in subtitles:
        folders = backup_folders(subtitle)
        if os.path.exists(subtitle):
            before = len(folders)
            prune_backups(subtitle)
            removed += before - len(backup_folders(subtitle))
        elif folders and os.path.getmtime(folders[-1]) < oldest_kept:
            for folder in folders:
                shutil.rmtree(folder)
            removed += len(folders)
    return removed


def folder_bytes(folder):
    return sum(os.path.getsize(os.path.join(root, name)) for root, _, names in os.walk(folder) for name in names)


def save_version(subtitle, folder, label):
    destination = os.path.join(folder, label + os.path.splitext(subtitle)[1])
    with open(destination, "xb") as handle:
        handle.write(read_regular(subtitle))
    return destination


def restore_version(subtitle, source):
    replace_file(subtitle, read_regular(source, confined=False))
