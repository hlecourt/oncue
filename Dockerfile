# SPDX-License-Identifier: GPL-3.0-or-later
FROM mwader/static-ffmpeg:9.0.2@sha256:7d9bdaaf887f7e6ce6151f67325c344074b5ff1fb75316011c3376503e449a7b AS ffmpeg

FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
ARG VERSION=dev
LABEL org.opencontainers.image.title="subtitle-guard" \
      org.opencontainers.image.description="Checks subtitle timing against a track embedded in the video and repairs it" \
      org.opencontainers.image.source="https://github.com/hlecourt/subtitle-guard" \
      org.opencontainers.image.licenses="GPL-3.0-or-later" \
      org.opencontainers.image.version="${VERSION}"
COPY --from=ffmpeg /ffmpeg /ffprobe /usr/local/bin/
WORKDIR /app
COPY requirements/docker.txt ./requirements.txt
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps --no-build-isolation . \
    && useradd --uid 1000 --user-group --no-create-home --shell /usr/sbin/nologin guard \
    && mkdir -p /config /backup && chown guard:guard /config /backup

ENV SUBTITLE_GUARD_STATE=/config SUBTITLE_GUARD_BACKUPS=/backup SUBTITLE_GUARD_UI_HOST=0.0.0.0 SUBTITLE_GUARD_FFSUBSYNC_PATH=""
USER 1000:1000
EXPOSE 8484
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8484/ready', timeout=4)" || exit 1
ENTRYPOINT ["subtitle-guard"]
CMD ["serve"]
