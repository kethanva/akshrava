#!/usr/bin/env bash
# Create a local encrypted-permissions PostgreSQL dump. Copy it to approved encrypted off-host
# storage separately; this script intentionally has no cloud-provider credentials.
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/.." && pwd)
env_file=${1:-"$repo_root/infra/.env"}
backup_dir=${2:-"$repo_root/backups"}
compose_file="$repo_root/infra/docker-compose.yml"

if [[ ! -f "$env_file" ]]; then
  echo "Missing deployment environment file: $env_file" >&2
  exit 1
fi

umask 077
mkdir -p "$backup_dir"
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
output="$backup_dir/akshrava-postgres-$timestamp.sql.gz"
temporary="$output.partial"

docker compose --env-file "$env_file" -f "$compose_file" --profile control-plane exec -T db \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | gzip -c > "$temporary"

if [[ ! -s "$temporary" ]]; then
  echo "Database dump was empty; retaining no completed backup." >&2
  exit 1
fi
mv "$temporary" "$output"
echo "Created $output. Copy it to approved encrypted off-host storage and test a restore."
