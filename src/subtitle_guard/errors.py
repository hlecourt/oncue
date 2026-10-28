"""Errors subtitle-guard raises on purpose, so callers can tell them from bugs."""


class SubtitleGuardError(Exception):
    pass


class UnsafePathError(SubtitleGuardError):
    pass


class SubtitleTooLargeError(SubtitleGuardError):
    pass


class FileChangedError(SubtitleGuardError):
    pass


class ProbeError(SubtitleGuardError):
    pass


class MatroskaError(SubtitleGuardError):
    pass


class ConfigError(SubtitleGuardError):
    pass


class UnknownJobKindError(SubtitleGuardError):
    pass
