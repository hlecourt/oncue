# SPDX-License-Identifier: GPL-3.0-or-later
"""The shapes the API sends and receives; the front's TypeScript types are generated from them."""

from pydantic import BaseModel, Field


class Problem(BaseModel):
    code: str
    message: str


class Credentials(BaseModel):
    user: str = Field(max_length=200)
    password: str = Field(max_length=1000)


class SessionState(BaseModel):
    login_required: bool
    user: str | None
    csrf: str | None


class Status(BaseModel):
    version: str
    jobs: dict[str, int]
    confidence: dict[str, int]
    to_review: int


class Readiness(BaseModel):
    ready: bool
    database: str
    workers: dict[str, bool] = {}


class ProposalSummary(BaseModel):
    kind: str
    confidence: int
    band: str
    changes: list[str]


class ReviewItem(BaseModel):
    id: str
    name: str
    folder: str
    decision: str
    reason: str
    tone: str
    confidence: int | None
    band: str
    checked: str
    proposal: ProposalSummary | None
    from_bazarr: bool


class Part(BaseModel):
    first: float
    last: float
    lines: int
    shift: float


class Version(BaseModel):
    id: str
    label: str
    time: str


class SubtitleRow(BaseModel):
    id: str
    name: str
    folder: str
    words: str
    tone: str
    confidence: int | None
    band: str
    checked: str


class SubtitleList(BaseModel):
    rows: list[SubtitleRow]
    total: int


class Sibling(BaseModel):
    id: str
    name: str
    words: str
    tone: str


class SubtitleDetail(BaseModel):
    id: str
    name: str
    folder: str
    video: str | None
    version: str
    decision: str | None
    words: str
    tone: str
    confidence: int | None
    band: str
    checked: str | None
    reference: str
    proposal: ProposalSummary | None
    parts: list[Part]
    scale: float
    versions: list[Version]
    from_bazarr: bool
    siblings: list[Sibling]


class Line(BaseModel):
    start: float
    end: float
    text: str


class Lines(BaseModel):
    version: str
    lines: list[Line]
    reference: list[Line]
    proposal: list[list[float]] | None


class HistoryRow(BaseModel):
    id: int
    time: str
    subtitle_id: str | None
    name: str
    folder: str
    decision: str
    words: str
    tone: str
    started_by: str
    changes: list[str]
    confidence: int | None
    band: str


class HistoryGroup(BaseModel):
    key: str
    label: str
    count: int


class History(BaseModel):
    rows: list[HistoryRow]
    groups: list[HistoryGroup]
    next_before: int | None


class Job(BaseModel):
    id: int
    kind: str
    status: str
    created: str
    started: str | None
    finished: str | None
    attempts: int
    decision: str | None
    summary: str | None
    error: str | None
    subtitle: str | None


class LogEvent(BaseModel):
    time: str
    level: str
    source: str
    event: str
    message: str
    details: dict


class VersionGuard(BaseModel):
    version: str = Field(min_length=40, max_length=40, pattern="^[0-9a-f]+$")


class Shift(VersionGuard):
    first: float = Field(ge=0, le=48 * 3600)
    last: float = Field(ge=0, le=48 * 3600)
    offset: float = Field(ge=-3600, le=3600)


class LineShift(BaseModel):
    first: int = Field(ge=0, le=200_000)
    last: int = Field(ge=0, le=200_000)
    offset: float = Field(ge=-3600, le=3600)


class Retime(VersionGuard):
    use_proposal: bool
    changes: list[LineShift] = Field(max_length=5000)


class Restore(VersionGuard):
    saved: str = Field(max_length=200)


class BulkAction(BaseModel):
    action: str = Field(pattern="^(keep|recheck|replacement)$")
    ids: list[str] = Field(min_length=1, max_length=500)


class CommandResult(BaseModel):
    message: str
    decision: str | None = None
    job: int | None = None


class BulkResult(BaseModel):
    done: list[str]
    skipped: dict[str, str]


class SettingView(BaseModel):
    name: str
    value: object
    is_set: bool
    source: str
    locked: bool
    restart: bool
    kind: str
    low: int
    high: int
    choices: list[str]
    note: str | None = None


class FixedValue(BaseModel):
    name: str
    value: object
    why: str


class SettingsView(BaseModel):
    settings: list[SettingView]
    fixed: list[FixedValue]


class SettingsChange(BaseModel):
    changes: dict[str, object] = Field(max_length=20)


class ApiKey(BaseModel):
    key: str


class Check(BaseModel):
    ok: bool
    detail: str


class SystemView(BaseModel):
    version: str
    checks: dict[str, Check]
    workers: dict[str, bool]
    backups_bytes: int
    mode: str


class SetupWarning(BaseModel):
    code: str
    message: str


class BazarrSetup(BaseModel):
    reachable: bool
    warnings: list[SetupWarning]


class Scan(BaseModel):
    id: int
    started: str
    source: str
    status: str
    total: int
    done: int
    failed: int
    not_settled: int


class CleanUp(BaseModel):
    removed: int
    backups_bytes: int


class AudioTrack(BaseModel):
    index: int
    language: str
    title: str
    codec: str
    channels: int
    default: bool
    commentary: bool


class AudioRequest(BaseModel):
    track: int | None = Field(None, ge=0, le=999)


class AudioState(BaseModel):
    ready: bool
    track: int | None
    tracks: list[AudioTrack]
    duration: float
    job: int | None
    peaks_per_second: int
