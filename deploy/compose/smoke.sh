#!/usr/bin/env bash
# End-to-end check of the compose stack with a built image: the API comes up healthy on PostgreSQL, the offline demo
# runs, replays and verifies inside it with its kernels in the sandbox service, which reaches nothing else, and a PI
# source syncs from a fake PI Web API into the lake and the catalog.
#   deploy/compose/smoke.sh alchemist:ci
set -euo pipefail
cd "$(dirname "$0")"
image="${1:?usage: smoke.sh <image>}"
cleanup() { docker compose logs api sandbox | tail -80 || true; docker compose down -v >/dev/null 2>&1 || true; rm -f .env; }
trap cleanup EXIT

cp .env.example .env
{ echo "POSTGRES_PASSWORD=smoke-$RANDOM"; echo "ALC_SANDBOX_TOKEN=smoke-$RANDOM$RANDOM"; echo "ALC_IMAGE=$image"; echo "ALC_API_MODEL=scripted"; } >> .env
docker compose up -d --wait api
curl -fsS "http://127.0.0.1:${ALC_PORT:-8000}/healthz" >/dev/null

# The demo brings its own users and documents.
docker compose exec -T -e ALC_ACCESS=/data/access.yaml -e ALC_KNOWLEDGE=/data/knowledge api \
  sh -c "alchemist demo setup && alchemist demo run" | tee /tmp/alc-demo.txt
grep -q "Verify original: True" /tmp/alc-demo.txt
# The kernels ran in the sandbox, which answers only with the token and reaches neither the database nor the internet.
docker compose logs sandbox | grep -q '"POST /start HTTP/1.1" 200'
test "$(docker compose exec -T api python -c "import urllib.request,urllib.error
try: urllib.request.urlopen(urllib.request.Request('http://sandbox:8888/stop', data=b'{}'))
except urllib.error.HTTPError as e: print(e.code)")" = 401
docker compose exec -T sandbox python -c "
import socket
for host, port in [('postgres', 5432), ('pypi.org', 443)]:
    try:
        socket.create_connection((host, port), timeout=5).close()
    except OSError:
        continue
    raise SystemExit(f'the sandbox reached {host}:{port}')"

docker compose cp ../../tests/fake_piwebapi.py api:/tmp/fake_piwebapi.py
docker compose exec -d -T api python -c "
import sys, time; sys.path.insert(0, '/tmp')
from fake_piwebapi import FakePIWebAPI
s = FakePIWebAPI(auth='basic'); s.thread.start(); open('/tmp/fakepi.url', 'w').write(s.url)
while True: time.sleep(60)"
sleep 3
docker compose exec -T -e PI_WEBAPI_PASSWORD=s3cret api sh -ec '
cat > /tmp/sources.yaml <<YAML
sources:
  - {id: p01-pi, plant: P01, kind: pi, url: "$(cat /tmp/fakepi.url)", auth: basic, username: reader,
     data_server: PISRV, tags: [FIC101.PV, TI102.PV]}
YAML
alchemist sources check --file /tmp/sources.yaml
alchemist sources sync --file /tmp/sources.yaml --start 2026-10-01T00:00Z --end 2026-10-01T04:00Z
test -f /data/lake/P01/p01-pi/historian/2026-10-01.parquet
alchemist catalog list | grep -q p01-pi-historian'
echo "compose smoke test passed"
