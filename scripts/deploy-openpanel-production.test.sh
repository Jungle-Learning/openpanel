#!/usr/bin/env bash

set -Eeuo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_directory="$(mktemp -d)"
stub_directory="${test_directory}/bin"
mkdir -p "$stub_directory"

cleanup() {
  rm -rf "$test_directory"
}
trap cleanup EXIT

cat > "${stub_directory}/docker" <<'STUB'
#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "$*" == buildx\ imagetools\ inspect* ]]; then
  echo "Name: test"
  echo "Digest: sha256:previous"
  exit 0
fi

if [[ "$*" == ps\ --quiet* || "$*" == ps\ --all\ --quiet* ]]; then
  echo "container-id"
  exit 0
fi

if [[ "$*" == inspect\ --format* ]]; then
  echo "sha256:running-image"
  exit 0
fi

if [[ "$*" == "inspect container-id" ]]; then
  echo '[{"Config":{"Env":["OPENAI_API_KEY=test","AI_MODEL=test","AI_REASONING_EFFORT=test","CLICKHOUSE_SETTINGS={}"]}}]'
  exit 0
fi

if [[ "$*" == image\ inspect* ]]; then
  exit 0
fi

echo "$*" >> "$TEST_DOCKER_CALLS"
STUB

cat > "${stub_directory}/curl" <<'STUB'
#!/usr/bin/env bash
set -Eeuo pipefail

url="${!#}"
echo "$url" >> "$TEST_CURL_CALLS"

case "$url" in
  *api-health*)
    if [[ "$TEST_SCENARIO" =~ ^rollback(-failure)?$ ]] \
      && [[ "$(grep -c '^deploy ' "$TEST_SSH_CALLS" 2>/dev/null || true)" -lt 2 ]]; then
      exit 22
    fi
    printf '{"ready":true,"releaseSha":"%s"}\n' "$GITHUB_SHA"
    ;;
  *dashboard-health*)
    printf '{"ok":true,"releaseSha":"%s"}\n' "$GITHUB_SHA"
    ;;
  *)
    echo "Unexpected curl URL: $url" >&2
    exit 1
    ;;
esac
STUB

cat > "${stub_directory}/ssh" <<'STUB'
#!/usr/bin/env bash
set -Eeuo pipefail

IFS= read -r operation
if [[ "$operation" == "prefetch" ]]; then
  IFS= read -r token
  [[ "$token" == "$GHCR_PULL_TOKEN" ]]
  IFS= read -r username
  [[ "$username" == "$GHCR_PULL_USERNAME" ]]
elif [[ "$operation" != "restore-local" && "$operation" != "deploy" ]]; then
  exit 1
fi
echo "$operation $*" >> "$TEST_SSH_CALLS"

if [[ "$TEST_SCENARIO" == rollback-failure ]] \
  && [[ "$operation" == "deploy" ]] \
  && [[ "$(grep -c '^deploy ' "$TEST_SSH_CALLS")" -ge 2 ]]; then
  exit 1
fi
STUB

chmod +x "${stub_directory}/docker" "${stub_directory}/curl" "${stub_directory}/ssh"

export PATH="${stub_directory}:${PATH}"
export GHCR_PULL_TOKEN="registry-token"
export GHCR_PULL_USERNAME="github-actions[bot]"
export GITHUB_SHA="0123456789abcdef0123456789abcdef01234567"
export OPENPANEL_API_HEALTH_URL="https://api-health.example.com"
export OPENPANEL_DASHBOARD_HEALTH_URL="https://dashboard-health.example.com"
export OPENPANEL_KNOWN_HOSTS_PATH="${test_directory}/known-hosts"
export OPENPANEL_PREFETCH_HOST="production.example.com"
export OPENPANEL_PREFETCH_SSH_KEY_PATH="${test_directory}/deploy-key"
export OPENPANEL_PREFETCH_USER="openpanel-deploy"
export TEST_DOCKER_CALLS="${test_directory}/docker-calls"
export TEST_CURL_CALLS="${test_directory}/curl-calls"
export TEST_SSH_CALLS="${test_directory}/ssh-calls"

touch "$OPENPANEL_KNOWN_HOSTS_PATH" "$OPENPANEL_PREFETCH_SSH_KEY_PATH"

printf 'prefetch\n%s\n%s\n' "$GHCR_PULL_TOKEN" "$GHCR_PULL_USERNAME" \
  | bash "${repository_root}/scripts/prefetch-openpanel-production-images.sh" \
    > "${test_directory}/prefetch-output"
grep -q "images prefetched" "${test_directory}/prefetch-output"
[[ "$(grep -c '^pull --quiet ghcr.io/jungle-learning/.*:production$' "$TEST_DOCKER_CALLS")" -eq 3 ]]
: > "$TEST_DOCKER_CALLS"

printf 'validate\n' \
  | bash "${repository_root}/scripts/prefetch-openpanel-production-images.sh" \
    > "${test_directory}/validate-output"
grep -q "configuration is valid" "${test_directory}/validate-output"
grep -q 'compose .* config --quiet' "$TEST_DOCKER_CALLS"
: > "$TEST_DOCKER_CALLS"

printf 'deploy\n' \
  | bash "${repository_root}/scripts/prefetch-openpanel-production-images.sh" \
    > "${test_directory}/deploy-output"
grep -q "stack deployed" "${test_directory}/deploy-output"
[[ "$(grep -c 'compose .* up --detach' "$TEST_DOCKER_CALLS")" -eq 2 ]]
: > "$TEST_DOCKER_CALLS"

TEST_SCENARIO=success bash "${repository_root}/scripts/deploy-openpanel-production.sh" \
  > "${test_directory}/success-output"

grep -q "is verified" "${test_directory}/success-output"
[[ "$(grep -c -- '--tag .*:production' "$TEST_DOCKER_CALLS")" -eq 3 ]]
[[ "$(wc -l < "$TEST_SSH_CALLS")" -eq 2 ]]

: > "$TEST_DOCKER_CALLS"
: > "$TEST_CURL_CALLS"
: > "$TEST_SSH_CALLS"

set +e
HEALTH_TIMEOUT_SECONDS=1 TEST_SCENARIO=rollback \
  bash "${repository_root}/scripts/deploy-openpanel-production.sh" \
  > "${test_directory}/rollback-output" 2>&1
rollback_exit_code=$?
set -e

[[ "$rollback_exit_code" -ne 0 ]]
grep -q "Rollback deployment is healthy" "${test_directory}/rollback-output"
[[ "$(grep -c -- '--tag .*:production' "$TEST_DOCKER_CALLS")" -eq 6 ]]
[[ "$(wc -l < "$TEST_SSH_CALLS")" -eq 4 ]]
[[ "$(grep -c '^prefetch ' "$TEST_SSH_CALLS")" -eq 1 ]]
[[ "$(grep -c '^restore-local ' "$TEST_SSH_CALLS")" -eq 1 ]]

: > "$TEST_DOCKER_CALLS"
: > "$TEST_CURL_CALLS"
: > "$TEST_SSH_CALLS"

set +e
HEALTH_TIMEOUT_SECONDS=1 TEST_SCENARIO=rollback-failure \
  bash "${repository_root}/scripts/deploy-openpanel-production.sh" \
  > "${test_directory}/rollback-failure-output" 2>&1
rollback_failure_exit_code=$?
set -e

[[ "$rollback_failure_exit_code" -ne 0 ]]
grep -q "Failed to deploy the rollback images" "${test_directory}/rollback-failure-output"
grep -q "Automatic rollback failed and requires operator attention" \
  "${test_directory}/rollback-failure-output"
if grep -q "Rollback deployment is healthy" "${test_directory}/rollback-failure-output"; then
  echo "Failed rollback was incorrectly reported as healthy" >&2
  exit 1
fi

echo "Production deployment script tests passed"
