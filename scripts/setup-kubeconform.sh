#!/usr/bin/env bash
# Install kubeconform binary with runner tool cache support.
set -euo pipefail

ARCH="${KUBECONFORM_ARCH:-amd64}"
VERSION="${KUBECONFORM_VERSION:-}"

# Resolve latest version if not specified
if [[ -z "$VERSION" ]]; then
  VERSION=$(curl -sL https://api.github.com/repos/yannh/kubeconform/releases/latest \
    | grep tag_name | sed -E 's/.*"v([^"]+)".*/\1/')
fi

BIN_URL="https://github.com/yannh/kubeconform/releases/download/v${VERSION}/kubeconform-linux-${ARCH}.tar.gz"

# Use runner tool cache if available, otherwise fall back to /usr/local/bin
if [[ -n "${RUNNER_TOOL_CACHE:-}" ]]; then
  BIN_DIR="$RUNNER_TOOL_CACHE/kubeconform/$VERSION/$ARCH"
  if [[ ! -x "$BIN_DIR/kubeconform" ]]; then
    mkdir -p "$BIN_DIR"
    curl -sL "$BIN_URL" | tar xz -C "$BIN_DIR"
    chmod +x "$BIN_DIR/kubeconform"
  fi
  echo "$BIN_DIR" >> "$GITHUB_PATH"
else
  # Local / CI without tool cache
  TMP=$(mktemp -d)
  curl -sL "$BIN_URL" | tar xz -C "$TMP"
  sudo install -m 0755 "$TMP/kubeconform" /usr/local/bin/kubeconform
  rm -rf "$TMP"
fi

echo "kubeconform v${VERSION} (${ARCH}) ready"
