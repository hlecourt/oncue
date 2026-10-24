# Configuration and operation

## Usage

```
subtitle-guard check VIDEO SUBTITLE [--apply]
subtitle-guard scan-series SERIES_ID [--apply]
subtitle-guard scan-movies [--apply]
subtitle-guard scan-all [--apply]
subtitle-guard needs-human
subtitle-guard hook VIDEO SUBTITLE LANGUAGE PROVIDER SUBTITLE_ID MEDIA_ID [SERIES_ID]
```

As a Bazarr post-processing command (Settings > Subtitles > Post-processing), with the package available to Bazarr's Python:

```
python -m subtitle_guard hook "{{episode}}" "{{subtitles}}" "{{subtitles_language_code2}}" "{{provider}}" "{{subtitle_id}}" "{{episode_id}}" "{{series_id}}"
```

Turn off Bazarr's own audio sync (`use_subsync`) when the guard is in place: the guard falls back to ffsubsync by itself for videos without any embedded subtitle track, and marks those results as not verified.

Settings come from the environment:

| Variable | Default |
|---|---|
| `SUBTITLE_GUARD_STATE` | `/config/subtitle-guard` |
| `SUBTITLE_GUARD_BACKUPS` | `/data/backup/subtitle-guard` |
| `SUBTITLE_GUARD_FFSUBSYNC_PATH` | `/app/bazarr/bin/libs` |
| `BAZARR_URL` | `http://localhost:6767` |
| `BAZARR_API_KEY` | read from `BAZARR_CONFIG` |
| `BAZARR_CONFIG` | `/config/config/config.yaml` |
