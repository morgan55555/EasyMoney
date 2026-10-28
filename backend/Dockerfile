FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Copy project metadata first so the dependency layer is cached until deps change.
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

# Remaining project files (migrations, entrypoint, alembic config).
COPY alembic.ini ./
COPY migrations ./migrations
COPY entrypoint.sh ./

# Non-root user; the uploads dir is a mounted volume.
RUN useradd -m appuser \
    && mkdir -p /data/uploads \
    && chown -R appuser:appuser /data /app
USER appuser

EXPOSE 8000

CMD ["sh", "entrypoint.sh"]
