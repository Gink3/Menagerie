#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${MENAGERIE_IMAGE:-menagerie:local}"
CONTAINER_NAME="${MENAGERIE_CONTAINER:-menagerie-local}"
HOST_PORT="${MENAGERIE_PORT:-8000}"
DATA_VOLUME="${MENAGERIE_VOLUME:-menagerie-local-data}"
SECRET_KEY="${MENAGERIE_SECRET_KEY:-menagerie-local-development-secret-change-me}"
ADMIN_USERNAME="${MENAGERIE_ADMIN_USERNAME:-localadmin}"
ADMIN_PASSWORD="${MENAGERIE_ADMIN_PASSWORD:-LocalDevelopmentPassword-ChangeMe!}"

case "$HOST_PORT" in
  ''|*[!0-9]*) echo "MENAGERIE_PORT must be numeric" >&2; exit 1 ;;
esac

echo "Building $IMAGE_NAME..."
docker build -t "$IMAGE_NAME" .

echo "Replacing $CONTAINER_NAME..."
docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  -p "127.0.0.1:${HOST_PORT}:8000" \
  -e "MENAGERIE_SECRET_KEY=$SECRET_KEY" \
  -e "MENAGERIE_ADMIN_USERNAME=$ADMIN_USERNAME" \
  -e "MENAGERIE_ADMIN_PASSWORD=$ADMIN_PASSWORD" \
  -v "${DATA_VOLUME}:/app/data" \
  "$IMAGE_NAME" >/dev/null

URL="http://127.0.0.1:${HOST_PORT}/"
echo "Waiting for $URL..."
for attempt in $(seq 1 30); do
  if curl --fail --silent --show-error "$URL" >/dev/null; then
    echo "Menagerie is running at $URL"
    echo "Container: $CONTAINER_NAME"
    echo "Data volume: $DATA_VOLUME"
    exit 0
  fi
  sleep 1
done

echo "Menagerie did not become healthy. Recent container logs:" >&2
docker logs --tail 80 "$CONTAINER_NAME" >&2
exit 1
