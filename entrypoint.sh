#!/bin/sh
# Seed the demo data on first boot (or after ephemeral storage resets, e.g.
# on Cloud Run cold starts) so the partner portal is never empty.
if [ ! -f /data/market.db ]; then
    python -m solar_scout.demo_seed --db /data/market.db
fi
exec python -m solar_scout.webui --host 0.0.0.0 --port "${PORT:-8080}" --base /data
