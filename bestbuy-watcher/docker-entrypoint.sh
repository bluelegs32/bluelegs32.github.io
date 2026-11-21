#!/bin/sh
set -e

CONFIG_PATH="${CONFIG_PATH:-/config/config.yml}"

mkdir -p "$(dirname "$CONFIG_PATH")" "$(dirname "${STATE_PATH:-/data/state.json}")"

if [ ! -f "$CONFIG_PATH" ]; then
  echo "No config at $CONFIG_PATH - seeding from config.example.yml."
  echo "Edit it, then restart this container."
  cp /app/config.example.yml "$CONFIG_PATH"
fi

exec "$@"
