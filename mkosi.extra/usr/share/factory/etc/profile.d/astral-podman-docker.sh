# Expose Podman's rootless Docker API socket to Docker-compatible developer tools.
if [ -n "${XDG_RUNTIME_DIR:-}" ]; then
    export DOCKER_HOST="${DOCKER_HOST:-unix://${XDG_RUNTIME_DIR}/podman/podman.sock}"
fi
