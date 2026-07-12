FROM python:3.12.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH
RUN python -m venv /opt/venv
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY alembic.ini ./
COPY migrations ./migrations
COPY knowledge ./knowledge
RUN addgroup --system app && adduser --system --ingroup app --uid 10001 app \
    && mkdir -p /app/data /app/releases /app/backups \
    && chown -R app:app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2)"
CMD ["uvicorn", "kid_terminal.app:app", "--host", "0.0.0.0", "--port", "8000", "--ws-max-size", "1048576"]
