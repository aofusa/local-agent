#!/bin/bash
# The chat tab's code sandbox on macOS: pull python:3.12-slim for whatever Linux engine the docker CLI reaches
# (Docker Desktop, Rancher Desktop's ~/.rd/bin/docker, colima ...). Runs use --pull never, so the image is fetched
# here. Without a docker command nothing is set up and the chat tab still writes code (it is not run).
. "$(dirname "$0")/lib/common.sh"
init_env
step "Docker"
docker="$(command -v docker || true)"
[ -n "$docker" ] || { warn "docker コマンドがありません。コードの実行は使えません（Docker Desktop / Rancher Desktop を入れてから再実行）"; exit 0; }
ok "$docker"
os="$(docker version --format '{{.Server.Os}}' 2>/dev/null || true)"
[ "$os" = "linux" ] || { warn "Docker のエンジンに届きません（Docker Desktop / Rancher Desktop を起動してから再実行してください）"; exit 0; }
step "イメージ python:3.12-slim"
docker pull --quiet python:3.12-slim
env_set SANDBOX_DOCKER "$docker"
ok "SANDBOX_DOCKER=$docker（.env）"
