FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLCONFIGDIR=/var/lib/cfizz/matplotlib

WORKDIR /app

# The deployment can override this at build time when the public PyPI endpoint
# is slow or unavailable, for example with a cloud-provider package mirror.
ARG PIP_INDEX_URL=https://pypi.org/simple

COPY pyproject.toml MANIFEST.in README.md LICENSE ./
COPY src ./src
COPY docs ./docs

RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install ".[agent]" "pyBigWig>=0.3"

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin cfizz \
    && mkdir -p /var/lib/cfizz /data /uploads /app/demo /app/references \
    && chown -R cfizz:cfizz /var/lib/cfizz /uploads

USER cfizz

EXPOSE 8000

CMD ["cfizz-agent", "--host", "0.0.0.0", "--port", "8000", "--runtime-dir", "/var/lib/cfizz", "--data-root", "/data", "--data-root", "/uploads", "--data-root", "/app/demo", "--data-root", "/app/references"]
