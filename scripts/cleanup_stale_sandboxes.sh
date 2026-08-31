#!/usr/bin/env bash
set -euo pipefail

mapfile -t container_ids < <(docker ps --all --quiet --filter label=faros.sandbox=true)
if ((${#container_ids[@]})); then
  docker rm --force "${container_ids[@]}" >/dev/null
fi
