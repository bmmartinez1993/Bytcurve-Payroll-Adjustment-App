#!/bin/bash
set -e
cd "$(dirname "$0")"
mkdir -p logs
echo "### Live diagnostic run (started $(date)) ###"
docker compose run --rm --build bytecurve-app
echo "### Live run finished (exit $? at $(date)) ###"
