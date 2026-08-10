#!/usr/bin/env bash

set -Eeuo pipefail

required_variables=(
  GHCR_PULL_TOKEN
  GHCR_PULL_USERNAME
  GITHUB_SHA
  OPENPANEL_API_HEALTH_URL
  OPENPANEL_DASHBOARD_HEALTH_URL
  OPENPANEL_KNOWN_HOSTS_PATH
  OPENPANEL_PREFETCH_HOST
  OPENPANEL_PREFETCH_SSH_KEY_PATH
  OPENPANEL_PREFETCH_USER
)

for variable_name in "${required_variables[@]}"; do
  if [[ -z "${!variable_name:-}" ]]; then
    echo "Missing required environment variable: ${variable_name}" >&2
    exit 1
  fi
done

if [[ ! "$GITHUB_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "GITHUB_SHA must be a full 40-character commit SHA" >&2
  exit 1
fi

HEALTH_TIMEOUT_SECONDS="${HEALTH_TIMEOUT_SECONDS:-300}"
REGISTRY_OWNER="${REGISTRY_OWNER:-jungle-learning}"

services=(api worker dashboard)
rollback_directory="$(mktemp -d)"
images_promoted=false
release_verified=false
rollback_in_progress=false

cleanup() {
  rm -rf "$rollback_directory"
}

image_name() {
  local service_name="$1"
  echo "ghcr.io/${REGISTRY_OWNER}/${service_name}"
}

current_manifest_digest() {
  local image_reference="$1"

  docker buildx imagetools inspect "$image_reference" 2>/dev/null \
    | awk '/^Digest:/ { print $2; exit }'
}

promote_images() {
  local service_name image previous_digest

  for service_name in "${services[@]}"; do
    image="$(image_name "$service_name")"
    previous_digest="$(current_manifest_digest "${image}:production" || true)"
    printf '%s' "$previous_digest" > "${rollback_directory}/${service_name}.digest"
  done

  images_promoted=true

  for service_name in "${services[@]}"; do
    image="$(image_name "$service_name")"
    echo "Promoting ${service_name} image for ${GITHUB_SHA}"
    docker buildx imagetools create \
      --tag "${image}:production" \
      "${image}:main-${GITHUB_SHA}"
  done
}

run_remote_operation() {
  ssh \
    -F /dev/null \
    -T \
    -i "$OPENPANEL_PREFETCH_SSH_KEY_PATH" \
    -o BatchMode=yes \
    -o ControlMaster=no \
    -o ControlPath=none \
    -o IdentityAgent=none \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=yes \
    -o "UserKnownHostsFile=${OPENPANEL_KNOWN_HOSTS_PATH}" \
    "${OPENPANEL_PREFETCH_USER}@${OPENPANEL_PREFETCH_HOST}"
}

prefetch_images() {
  printf 'prefetch\n%s\n%s\n' "$GHCR_PULL_TOKEN" "$GHCR_PULL_USERNAME" \
    | run_remote_operation
}

restore_local_images() {
  printf 'restore-local\n' \
    | run_remote_operation
}

deploy_stack() {
  printf 'deploy\n' \
    | run_remote_operation
}

wait_for_release_health() {
  local deadline api_ready dashboard_ready

  deadline=$((SECONDS + HEALTH_TIMEOUT_SECONDS))
  while (( SECONDS < deadline )); do
    api_ready=false
    dashboard_ready=false

    if curl --fail --silent --show-error "$OPENPANEL_API_HEALTH_URL" \
      | jq -e --arg release_sha "$GITHUB_SHA" \
        '.ready == true and .releaseSha == $release_sha' > /dev/null 2>&1; then
      api_ready=true
    fi

    if curl --fail --silent --show-error "$OPENPANEL_DASHBOARD_HEALTH_URL" \
      | jq -e --arg release_sha "$GITHUB_SHA" \
        '.ok == true and .releaseSha == $release_sha' > /dev/null 2>&1; then
      dashboard_ready=true
    fi

    if [[ "$api_ready" == true && "$dashboard_ready" == true ]]; then
      release_verified=true
      echo "Public API and dashboard are serving release ${GITHUB_SHA}"
      return 0
    fi

    sleep 5
  done

  echo "Timed out waiting for public endpoints to serve ${GITHUB_SHA}" >&2
  return 1
}

restore_previous_images() {
  local service_name image previous_digest

  for service_name in "${services[@]}"; do
    previous_digest="$(cat "${rollback_directory}/${service_name}.digest")"
    if [[ -z "$previous_digest" ]]; then
      echo "No previous production manifest exists for ${service_name}" >&2
      return 1
    fi
  done

  for service_name in "${services[@]}"; do
    previous_digest="$(cat "${rollback_directory}/${service_name}.digest")"
    image="$(image_name "$service_name")"
    echo "Restoring previous ${service_name} image"
    docker buildx imagetools create \
      --tag "${image}:production" \
      "${image}@${previous_digest}"
  done
}

rollback_release() {
  rollback_in_progress=true
  echo "Release verification failed; rolling back production images" >&2

  if ! restore_previous_images; then
    echo "Failed to restore one or more previous registry manifests" >&2
  fi

  # Prefetch captured this snapshot before the failed release was deployed.
  # Do not prefetch again here or the helper would replace it with failed images.
  if ! restore_local_images; then
    echo "Failed to restore the local rollback snapshot" >&2
    return 1
  fi

  if ! deploy_stack; then
    echo "Failed to deploy the rollback images" >&2
    return 1
  fi

  if ! curl --fail --silent --show-error "$OPENPANEL_API_HEALTH_URL" > /dev/null; then
    echo "API health check failed after rollback" >&2
    return 1
  fi

  if ! curl --fail --silent --show-error "$OPENPANEL_DASHBOARD_HEALTH_URL" > /dev/null; then
    echo "Dashboard health check failed after rollback" >&2
    return 1
  fi

  echo "Rollback deployment is healthy"
}

on_exit() {
  local exit_code=$?

  if (( exit_code != 0 )) \
    && [[ "$images_promoted" == true ]] \
    && [[ "$release_verified" == false ]] \
    && [[ "$rollback_in_progress" == false ]]; then
    set +e
    rollback_release
    rollback_exit_code=$?
    set -e
    if (( rollback_exit_code != 0 )); then
      echo "Automatic rollback failed and requires operator attention" >&2
    fi
  fi

  cleanup
  exit "$exit_code"
}

trap on_exit EXIT

promote_images
prefetch_images
deploy_stack
wait_for_release_health

echo "OpenPanel production release ${GITHUB_SHA} is verified"
