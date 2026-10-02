#!/bin/sh
set -eu

PROJECT_NAME="dampp"
INSTALL_DIR=${DAMPP_INSTALL_DIR:-"$HOME/.local/share/$PROJECT_NAME"}
BIN_DIR=${DAMPP_BIN_DIR:-"$HOME/.local/bin"}
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/$PROJECT_NAME"

# stop and remove the containers; the database volume is kept
for runtime in docker podman; do
    if command -v "$runtime" >/dev/null 2>&1; then
        "$runtime" compose -p "$PROJECT_NAME" down >/dev/null 2>&1 || true
        break
    fi
done

rm -f "$BIN_DIR/$PROJECT_NAME"
rm -rf "$INSTALL_DIR" "$CONFIG_DIR"

echo "Uninstalled dampp."
echo "Your web root (~/dampp/www by default) and the database volume were kept."
echo "To delete the database too: docker volume rm dampp_mariadb-data"
