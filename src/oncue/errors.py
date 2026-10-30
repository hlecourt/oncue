# SPDX-License-Identifier: GPL-3.0-or-later
"""Errors OnCue raises on purpose, so callers can tell them from bugs."""


class OnCueError(Exception):
    pass


class UnsafePathError(OnCueError):
    pass


class SubtitleTooLargeError(OnCueError):
    pass


class MatroskaError(OnCueError):
    pass


class ConfigError(OnCueError):
    pass


class UnknownJobKindError(OnCueError):
    pass


class ToolError(OnCueError):
    pass


class ProbeError(ToolError):
    pass


class EditRefusedError(OnCueError):
    pass


class StaleVersionError(EditRefusedError):
    pass
