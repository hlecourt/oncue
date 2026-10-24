FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg util-linux \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[audio]"

ENV SUBTITLE_GUARD_STATE=/config SUBTITLE_GUARD_BACKUPS=/backup
ENTRYPOINT ["subtitle-guard"]
