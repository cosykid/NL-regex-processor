#!/usr/bin/env bash
set -e

# Resolve JAVA_HOME for whatever architecture this image was built on
# (java-17-openjdk-arm64 vs -amd64) so PySpark can find the JVM.
export JAVA_HOME="${JAVA_HOME:-$(dirname "$(dirname "$(readlink -f "$(which java)")")")}"

case "${1:-web}" in
  web)
    echo "Applying database migrations..."
    python manage.py migrate --noinput
    python manage.py collectstatic --noinput >/dev/null 2>&1 || true
    echo "Starting gunicorn..."
    exec gunicorn config.wsgi:application \
        --bind 0.0.0.0:8000 \
        --workers "${GUNICORN_WORKERS:-3}" \
        --timeout 120
    ;;
  worker)
    echo "Starting Celery worker (Spark master: ${SPARK_MASTER_URL:-local[*]})..."
    exec celery -A config worker \
        --loglevel="${CELERY_LOGLEVEL:-info}" \
        --concurrency="${CELERY_CONCURRENCY:-2}"
    ;;
  flower)
    exec celery -A config flower --port=5555
    ;;
  *)
    exec "$@"
    ;;
esac
