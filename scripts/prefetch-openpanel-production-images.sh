#!/usr/bin/env bash

set -Eeuo pipefail

compose_project="r11wls882emmyr2r3n1wvihp"
service_directory="/data/coolify/services/${compose_project}"
runtime_env_file=""
docker_config_directory=""
compose_files=()

cleanup() {
  if [[ -n "$docker_config_directory" ]]; then
    rm -rf "$docker_config_directory"
  fi
  if [[ -n "$runtime_env_file" ]]; then
    rm -f "$runtime_env_file"
  fi
}
trap cleanup EXIT

prepare_compose_environment() {
  local -a container_ids

  if [[ -n "$runtime_env_file" ]]; then
    return
  fi

  while IFS= read -r container_id; do
    container_ids+=("$container_id")
  done < <(
      docker ps --all --quiet \
        --filter "label=com.docker.compose.project=${compose_project}"
    )
  if (( ${#container_ids[@]} == 0 )); then
    echo "No existing OpenPanel containers were found for runtime configuration" >&2
    exit 1
  fi

  runtime_env_file="$(mktemp /tmp/openpanel-runtime.XXXXXX)"
  chmod 0600 "$runtime_env_file"
  docker inspect "${container_ids[@]}" \
    | jq -r \
      '[.[].Config.Env[] | capture("^(?<key>[^=]+)=(?<value>.*)$")] | unique_by(.key)[] | "\(.key)=" + (.value | @json)' \
      > "$runtime_env_file"

  compose_files=(
    --env-file "$runtime_env_file"
    --project-directory "$service_directory"
    --project-name "$compose_project"
    --file "${service_directory}/docker-compose.yml"
    --file "${service_directory}/openpanel-production-runtime.yml"
    --file "${service_directory}/openpanel-production-images.yml"
  )
}

save_local_rollback_images() {
  local service_name container_id image_id

  for service_name in api worker dashboard; do
    container_id="$(
      docker ps --quiet \
        --filter "label=com.docker.compose.project=${compose_project}" \
        --filter "label=com.docker.compose.service=openpanel-${service_name}"
    )"
    if [[ -z "$container_id" ]]; then
      echo "Running openpanel-${service_name} container was not found" >&2
      exit 1
    fi

    image_id="$(docker inspect --format '{{.Image}}' "$container_id")"
    docker tag "$image_id" "ghcr.io/jungle-learning/${service_name}:rollback"
  done
}

restore_local_rollback_images() {
  local service_name

  for service_name in api worker dashboard; do
    docker image inspect "ghcr.io/jungle-learning/${service_name}:rollback" > /dev/null
    docker tag \
      "ghcr.io/jungle-learning/${service_name}:rollback" \
      "ghcr.io/jungle-learning/${service_name}:production"
  done

  echo "OpenPanel local rollback images restored"
}

authenticate_with_ghcr() {
  local ghcr_token ghcr_username

  IFS= read -r ghcr_token
  if [[ -z "$ghcr_token" ]]; then
    echo "A GitHub Container Registry token is required" >&2
    exit 1
  fi

  IFS= read -r ghcr_username
  if [[ ! "$ghcr_username" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*(\[bot\])?$ ]]; then
    echo "A valid GitHub Container Registry username is required" >&2
    exit 1
  fi

  docker_config_directory="$(mktemp -d /tmp/openpanel-docker-config.XXXXXX)"
  chmod 0700 "$docker_config_directory"
  export DOCKER_CONFIG="$docker_config_directory"
  printf '%s' "$ghcr_token" \
    | docker login ghcr.io --username "$ghcr_username" --password-stdin > /dev/null
  unset ghcr_token
  unset ghcr_username
}

publish_local_production_images() {
  local service_name

  authenticate_with_ghcr
  for service_name in api worker dashboard; do
    docker push --quiet "ghcr.io/jungle-learning/${service_name}:production"
  done

  echo "OpenPanel restored production images published"
}

validate_stack() {
  prepare_compose_environment
  docker compose "${compose_files[@]}" config --quiet
  echo "OpenPanel production Compose configuration is valid"
}

deploy_stack() {
  validate_stack > /dev/null
  docker compose "${compose_files[@]}" up \
    --detach \
    --no-deps \
    --wait \
    --wait-timeout 300 \
    openpanel-api
  docker compose "${compose_files[@]}" up \
    --detach \
    --no-deps \
    --wait \
    --wait-timeout 300 \
    openpanel-worker \
    openpanel-dashboard
  echo "OpenPanel production stack deployed"
}

IFS= read -r operation
if [[ "$operation" == "restore-local" ]]; then
  restore_local_rollback_images
  exit 0
fi

if [[ "$operation" == "publish-local" ]]; then
  publish_local_production_images
  exit 0
fi

if [[ "$operation" == "validate" ]]; then
  validate_stack
  exit 0
fi

if [[ "$operation" == "deploy" ]]; then
  deploy_stack
  exit 0
fi

if [[ "$operation" != "prefetch" ]]; then
  echo "Unsupported operation" >&2
  exit 1
fi

save_local_rollback_images
authenticate_with_ghcr

for service_name in api worker dashboard; do
  docker pull --quiet "ghcr.io/jungle-learning/${service_name}:production"
done

echo "OpenPanel production images prefetched"
