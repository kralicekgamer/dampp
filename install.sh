#!/bin/sh
set -eu

PROJECT_NAME="dampp"
INSTALL_DIR=${DAMPP_INSTALL_DIR:-"$HOME/.local/share/$PROJECT_NAME"}
BIN_DIR=${DAMPP_BIN_DIR:-"$HOME/.local/bin"}
REPO_URL=${DAMPP_REPO_URL:-"https://github.com/kralicekgamer/dampp/archive/refs/heads/master.tar.gz"}
REPO_DIR="$INSTALL_DIR/repo"
VENV_DIR="$INSTALL_DIR/.venv"
WRAPPER="$BIN_DIR/$PROJECT_NAME"

for tool in python3 curl tar; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "dampp: $tool is required" >&2
        exit 1
    }
done

if ! command -v docker >/dev/null 2>&1 && ! command -v podman >/dev/null 2>&1; then
    echo "dampp: Docker (or Podman) with the compose plugin is required" >&2
    exit 1
fi

mkdir -p "$INSTALL_DIR" "$BIN_DIR"
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT INT TERM

curl -fsSL "$REPO_URL" -o "$TEMP_DIR/dampp.tar.gz"
tar -xzf "$TEMP_DIR/dampp.tar.gz" -C "$TEMP_DIR"
EXTRACTED_DIR=$(find "$TEMP_DIR" -mindepth 1 -maxdepth 1 -type d -print -quit)
if [ -z "$EXTRACTED_DIR" ]; then
    echo "dampp: downloaded archive is empty" >&2
    exit 1
fi
rm -rf "$REPO_DIR"
mv "$EXTRACTED_DIR" "$REPO_DIR"

if [ ! -x "$VENV_DIR/bin/python" ]; then
    python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip >/dev/null
"$VENV_DIR/bin/python" -m pip install --upgrade -r "$REPO_DIR/requirements.txt"

cat > "$WRAPPER" <<WRAPPER_EOF
#!/bin/sh
exec "$VENV_DIR/bin/python" "$REPO_DIR/dampp.py" "\$@"
WRAPPER_EOF
chmod +x "$WRAPPER"

echo "Installed dampp to $WRAPPER"
case ":${PATH:-}:" in
    *:"$BIN_DIR":*) ;;
    *) echo "Add $BIN_DIR to PATH to run dampp from any shell." ;;
esac
