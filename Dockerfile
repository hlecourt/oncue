# SPDX-License-Identifier: GPL-3.0-or-later
FROM mwader/static-ffmpeg:9.0.2-1@sha256:f5d100184884eb3e82b0eb14bb5ebf98da99826ac824989720297fb9ef21c822 AS ffmpeg

FROM --platform=$BUILDPLATFORM node:24.22.0-slim@sha256:91acba91b842bec0bf7ea6f5e09c644c7ccad72c4996ed9616d861d1c7cd60d4 AS front
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
WORKDIR /build/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN corepack pnpm@12.16.1 install --frozen-lockfile
COPY frontend ./
RUN corepack pnpm@12.16.1 run build

FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
ARG VERSION=dev
LABEL org.opencontainers.image.title="oncue" \
      org.opencontainers.image.description="Checks subtitle timing against a track embedded in the video and repairs it" \
      org.opencontainers.image.source="https://github.com/hlecourt/oncue" \
      org.opencontainers.image.licenses="GPL-3.0-or-later" \
      org.opencontainers.image.version="${VERSION}"
COPY --from=ffmpeg /ffmpeg /ffprobe /usr/local/bin/
WORKDIR /app
COPY requirements/docker.txt ./requirements.txt
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY --from=front /build/src/oncue/api/static ./src/oncue/api/static
RUN pip install --no-cache-dir --no-deps --no-build-isolation . \
    && useradd --uid 1000 --user-group --no-create-home --shell /usr/sbin/nologin oncue \
    && mkdir -p /config /backup && chown oncue:oncue /config /backup

ENV ONCUE_STATE=/config ONCUE_BACKUPS=/backup ONCUE_UI_HOST=0.0.0.0 ONCUE_FFSUBSYNC_PATH=""
USER 1000:1000
EXPOSE 8484
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8484/ready', timeout=4)" || exit 1
ENTRYPOINT ["oncue"]
CMD ["serve"]
