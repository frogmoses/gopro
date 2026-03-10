#!/usr/bin/env bash
# Deploy gopro sentinel to the roaster machine.
#
# Two modes:
#   ./deploy.sh         — rsync only changed .py files (~1s)
#   ./deploy.sh --full  — rsync all files + reinstall deps
#
# Requires SSH alias "roaster" configured in ~/.ssh/config

set -euo pipefail

REMOTE="${DEPLOY_SSH_HOST:?Set DEPLOY_SSH_HOST to your roaster SSH alias or user@host}"
REMOTE_DIR="${DEPLOY_REMOTE_DIR:-~/CodeProjects/gopro}"
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"

# Color output helpers
green() { printf '\033[32m%s\033[0m\n' "$1"; }
yellow() { printf '\033[33m%s\033[0m\n' "$1"; }

if [[ "${1:-}" == "--full" ]]; then
    # Full deploy: rsync everything (code, reference images) + reinstall deps
    yellow "Full deploy: syncing all project files..."
    rsync -avz \
        --exclude='__pycache__/' --exclude='.venv/' --exclude='captures/' \
        --exclude='*.pyc' \
        "$LOCAL_DIR/" "$REMOTE:$REMOTE_DIR/"

    yellow "Reinstalling dependencies..."
    ssh "$REMOTE" "source ~/.local/bin/env && cd $REMOTE_DIR && uv pip install open-gopro anthropic websockets numpy pillow"

    green "Full deploy complete."
else
    # Quick deploy: rsync .py files and docs (~1s)
    yellow "Quick deploy: syncing .py files to roaster..."
    rsync -avz "$LOCAL_DIR/"*.py "$REMOTE:$REMOTE_DIR/"

    green "Quick deploy complete."
fi

# Show what's running on roaster
echo ""
yellow "Verifying deployment..."
ssh "$REMOTE" "ls -la $REMOTE_DIR/*.py | wc -l" | xargs -I{} echo "  {} Python files deployed"
