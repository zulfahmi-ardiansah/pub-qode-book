# Qodebook — the reader, served.
#
# The mapper is deliberately not in this image. It writes to the database, and
# nothing in this container is allowed to: the app opens every connection
# read-only, and the process it runs as owns nothing it could write to anyway.

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    DATA_MODE=sqlite \
    SQLITE_PATH=data/database.sqlite

WORKDIR /app

# Dependencies first, in their own layer — they change far less often than the
# app does, so a code edit does not reinstall them.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY serve/ ./serve/
COPY data/structure.sql data/database.sqlite ./data/

RUN useradd --create-home --uid 10001 qodebook && chown -R qodebook:qodebook /app
USER qodebook

# The default. Override with -e PORT=9000 — the CMD reads it at run time.
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/').read(1)"

# Shell form, so ${PORT} is expanded by the shell at start-up rather than frozen
# into the image at build time.
CMD ["sh", "-c", "exec uvicorn serve.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
