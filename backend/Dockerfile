# Stage 1: Build
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
ARG INSTALL_YOLO=false
ARG INSTALL_CLOUD_PROVIDER=none
COPY pyproject.toml ./
COPY akshrava_backend ./akshrava_backend
COPY migrations ./migrations
COPY alembic.ini ./
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --upgrade "pip<25" && \
    pip install "gunicorn>=23,<24" && \
    if [ "$INSTALL_YOLO" = "true" ]; then pip install '.[yolo]'; else pip install .; fi && \
    case "$INSTALL_CLOUD_PROVIDER" in \
      none) ;; \
      aws) pip install '.[aws]' ;; \
      gcp) pip install '.[gcp]' ;; \
      azure) pip install '.[azure]' ;; \
      *) echo "unknown INSTALL_CLOUD_PROVIDER=$INSTALL_CLOUD_PROVIDER" >&2; exit 1 ;; \
    esac

# Stage 2: Runtime
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Create a non-root user
RUN addgroup --system appgroup && adduser --system --group appuser

# Copy virtualenv and application code
COPY --from=builder /opt/venv /opt/venv
COPY akshrava_backend ./akshrava_backend
COPY migrations ./migrations
COPY alembic.ini ./

# Give appuser ownership
RUN chown -R appuser:appgroup /app

USER appuser
EXPOSE 8000

# Use Gunicorn to manage Uvicorn workers
CMD ["gunicorn", "akshrava_backend.main:app", "--workers", "1", "--worker-class", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "--timeout", "30", "--graceful-timeout", "30", "--access-logfile", "-", "--error-logfile", "-"]
