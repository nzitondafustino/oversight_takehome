#!/usr/bin/env bash
# Run the complete backend suite, including real MongoDB tests, without application data or paid calls.
set -euo pipefail
cd "$(dirname "$0")"

if [[ -n "${EXPENSE_API_TEST_MONGO_URI:-}" ]]; then
  exec .venv/bin/python -m pytest "$@"
fi

# A random localhost port avoids the application database. --rm removes the container's test data.
backend_test_container=$(docker run --rm -d -p 127.0.0.1::27017 mongo:7.0)
cleanup() { docker stop "$backend_test_container" >/dev/null; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

backend_test_ready=false
for ((attempt = 0; attempt < 30; attempt++)); do
  if docker exec "$backend_test_container" mongosh --quiet --eval 'db.adminCommand({ping:1}).ok' >/dev/null 2>&1; then
    backend_test_ready=true
    break
  fi
  sleep 0.5
done
if [[ "$backend_test_ready" != true ]]; then
  echo "Disposable MongoDB did not become ready" >&2
  exit 1
fi

backend_test_address=$(docker port "$backend_test_container" 27017/tcp)
export EXPENSE_API_TEST_MONGO_URI="mongodb://$backend_test_address"
.venv/bin/python -m pytest "$@"
