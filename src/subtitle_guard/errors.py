# SPDX-License-Identifier: GPL-3.0-or-later
"""Errors subtitle-guard raises on purpose, so callers can tell them from bugs."""


class SubtitleGuardError(Exception):
    pass


class UnsafePathError(SubtitleGuardError):
    pass


class SubtitleTooLargeError(SubtitleGuardError):
    pass


class MatroskaError(SubtitleGuardError):
    pass


class ConfigError(SubtitleGuardError):
    pass


class UnknownJobKindError(SubtitleGuardError):
    pass


class ToolError(SubtitleGuardError):
    pass


class ProbeError(ToolError):
    pass


class EditRefusedError(SubtitleGuardError):
    pass
