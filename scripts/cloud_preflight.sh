#!/usr/bin/env bash
# Validate only deployment inputs. This script never prints secret values.
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/.." && pwd)
env_file=${1:-"$repo_root/infra/.env"}
field_mode=${2:-}
compose_file="$repo_root/infra/docker-compose.yml"

if [[ ! -f "$env_file" ]]; then
  echo "Missing deployment environment file: $env_file" >&2
  exit 1
fi

value_for() {
  local name=$1
  sed -n "s/^${name}=//p" "$env_file" | tail -n 1
}

require_secret() {
  local name=$1 value
  value=$(value_for "$name")
  if [[ ${#value} -lt 32 ]] || [[ "$value" == *replace-with* ]] || [[ "$value" == *example* ]]; then
    echo "$name must be a non-example secret of at least 32 characters." >&2
    exit 1
  fi
}

require_secret POSTGRES_PASSWORD
require_secret JWT_SECRET
require_secret GRAFANA_PASSWORD

postgres_password=$(value_for POSTGRES_PASSWORD)
if [[ ! "$postgres_password" =~ ^[A-Za-z0-9_-]+$ ]]; then
  echo "POSTGRES_PASSWORD must use URL-safe characters because it is part of DATABASE_URL." >&2
  exit 1
fi

domain=$(value_for DOMAIN)
if [[ -z "$domain" ]] || [[ "$domain" == *example.org ]] || [[ "$domain" == *example.com ]]; then
  echo "DOMAIN must be the real public DNS name before deployment." >&2
  exit 1
fi

detector=$(value_for DETECTOR)
case "$detector" in
  noop|ultralytics|remote) ;;
  *) echo "DETECTOR must be noop, ultralytics or remote." >&2; exit 1 ;;
esac

environment=$(value_for AKSHRAVA_ENV)
if [[ "$environment" != "pilot" && "$environment" != "production" ]]; then
  echo "AKSHRAVA_ENV must be pilot or production for deployment." >&2
  exit 1
fi
if [[ "$(value_for DEV_AUTH_BYPASS)" == "true" ]]; then
  echo "DEV_AUTH_BYPASS must be false for deployment." >&2
  exit 1
fi

if [[ "$field_mode" == "--field" && "$detector" != "ultralytics" && "$detector" != "remote" ]]; then
  echo "--field requires DETECTOR=ultralytics or DETECTOR=remote; noop is transport-only bench mode." >&2
  exit 1
fi

if [[ "$detector" == "remote" ]]; then
  remote_url=$(value_for REMOTE_INFERENCE_URL)
  remote_secret=$(value_for REMOTE_WORKER_SECRET)
  if [[ ! "$remote_url" =~ ^https:// ]] || [[ ${#remote_secret} -lt 32 ]] || [[ "$remote_secret" == *replace-with* ]]; then
    echo "DETECTOR=remote requires an HTTPS REMOTE_INFERENCE_URL and a non-example REMOTE_WORKER_SECRET of at least 32 characters." >&2
    exit 1
  fi
fi

if [[ "$field_mode" == "--gpu-worker" ]]; then
  [[ ${#$(value_for REMOTE_WORKER_SECRET)} -ge 32 ]] || {
    echo "--gpu-worker requires REMOTE_WORKER_SECRET of at least 32 characters." >&2; exit 1;
  }
  [[ "$(value_for INSTALL_YOLO)" == "true" ]] || {
    echo "--gpu-worker requires INSTALL_YOLO=true." >&2; exit 1;
  }
  [[ "$(value_for YOLO_WEIGHTS)" == /models/* ]] || {
    echo "--gpu-worker requires a YOLO_WEIGHTS path under /models/." >&2; exit 1;
  }
fi

cloud_provider=$(value_for CLOUD_FALLBACK_PROVIDER)
case "$cloud_provider" in
  ""|none) ;;
  aws)
    [[ -n "$(value_for AWS_REGION)" ]] || { echo "AWS_REGION is required for AWS fallback." >&2; exit 1; }
    ;;
  gcp) ;;
  azure)
    [[ -n "$(value_for AZURE_VISION_ENDPOINT)" && -n "$(value_for AZURE_VISION_KEY)" ]] || {
      echo "Azure fallback requires AZURE_VISION_ENDPOINT and AZURE_VISION_KEY." >&2; exit 1;
    }
    ;;
  *) echo "CLOUD_FALLBACK_PROVIDER must be none, aws, gcp or azure." >&2; exit 1 ;;
esac

if [[ "$detector" == "ultralytics" || "$field_mode" == "--gpu-worker" ]]; then
  install_yolo=$(value_for INSTALL_YOLO)
  model_dir=$(value_for MODEL_DIR)
  weights=$(value_for YOLO_WEIGHTS)
  if [[ "$install_yolo" != "true" ]] || [[ "$weights" != /models/* ]]; then
    echo "ultralytics requires INSTALL_YOLO=true and a YOLO_WEIGHTS path under /models/." >&2
    exit 1
  fi
  model_name=${weights#/models/}
  if [[ ! -f "$(dirname "$env_file")/$model_dir/$model_name" ]]; then
    echo "Approved model file is missing from MODEL_DIR; refusing field deployment." >&2
    exit 1
  fi
fi

docker compose --env-file "$env_file" -f "$compose_file" config --quiet
echo "Deployment preflight passed (${detector} detector)."
