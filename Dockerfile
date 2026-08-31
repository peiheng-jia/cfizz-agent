FROM python:3.11-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Both package mirrors remain overridable so the same image can be built
# outside Tencent Cloud without editing this file.
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG DEBIAN_MIRROR=https://deb.debian.org/debian
ARG DEBIAN_SECURITY_MIRROR=https://deb.debian.org/debian-security

RUN sed -i \
      -e "s|http://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" \
      -e "s|http://deb.debian.org/debian|${DEBIAN_MIRROR}|g" \
      /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml MANIFEST.in README.md LICENSE ./
COPY src ./src

RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip wheel --wheel-dir /wheels ".[agent]" "pyBigWig>=0.3"


FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLCONFIGDIR=/var/lib/cfizz/matplotlib

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-index --find-links=/wheels "cfizz[agent]" pyBigWig \
    && rm -rf /wheels

# The demo endpoint reads its FigureSpec from docs at runtime. Large input and
# reference directories are mounted separately by Compose.
COPY docs ./docs

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin cfizz \
    && mkdir -p /var/lib/cfizz /data /uploads /app/demo /app/references \
    && chown -R cfizz:cfizz /var/lib/cfizz /uploads

USER cfizz

EXPOSE 8000

CMD ["cfizz-agent", "--host", "0.0.0.0", "--port", "8000", "--runtime-dir", "/var/lib/cfizz", "--data-root", "/data", "--data-root", "/uploads", "--data-root", "/app/demo", "--data-root", "/app/references"]
