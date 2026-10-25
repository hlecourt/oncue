FROM mwader/static-ffmpeg:9.0.2 AS ffmpeg

FROM python:3.12-slim
COPY --from=ffmpeg /ffmpeg /ffprobe /usr/local/bin/
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[audio]"

ENV SUBTITLE_GUARD_STATE=/config SUBTITLE_GUARD_BACKUPS=/backup SUBTITLE_GUARD_UI_HOST=0.0.0.0 SUBTITLE_GUARD_FFSUBSYNC_PATH=""
EXPOSE 8484
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8484/health', timeout=4)" || exit 1
ENTRYPOINT ["subtitle-guard"]
CMD ["serve"]
