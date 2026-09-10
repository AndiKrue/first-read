#!/usr/bin/env bash
set -eu

env_file="${1:-.env}"
if [ ! -f "$env_file" ]; then
  echo "Deployment environment file not found: $env_file" >&2
  exit 1
fi

# Convert dotenv assignments without printing their values. Comments and blank
# lines are ignored; values remain opaque arguments to gcloud.
env_vars="$({
  awk '
    BEGIN { printf "^~^" }
    /^[[:space:]]*($|#)/ { next }
    {
      sub(/^[[:space:]]*export[[:space:]]+/, "")
      sub(/\r$/, "")
      if (index($0, "=") == 0) next
      printf "%s%s", separator, $0
      separator="~"
    }
  ' "$env_file"
})"

gcloud run deploy first-read \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --memory 2Gi \
  --timeout 900 \
  --max-instances 3 \
  --concurrency 4 \
  --no-cpu-throttling \
  --set-env-vars "$env_vars"
