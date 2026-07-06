#!/usr/bin/env bash
# Classic installer for @Duckspec/@DuckTools — clones the framework once and
# wires up a dependency-free `ducktools` binary + the shared settings registry.
# No pip, no venv: @DuckToolsApp is pure stdlib, so a tiny wrapper script is enough.
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "usage: install.sh <path-to-clone-duckspec-into>" >&2
  exit 1
fi

CLONE_PATH="$1"
REPO_URL="git@github.com:komorebinator/duckspec.git"
SETTINGS_DIR="$HOME/.duckspec"

# Pick whichever conventional bin dir is already on PATH (covers Linux's
# ~/.local/bin, Homebrew's /opt/homebrew/bin or /usr/local/bin on macOS,
# and anything else the user's shell already set up) — no OS detection
# needed, just ask PATH what it actually uses. Falls back to ~/.local/bin
# if none of them are present yet, same as before.
BIN_DIR=""
for candidate in "$HOME/.local/bin" "$HOME/bin" "/opt/homebrew/bin" "/usr/local/bin"; do
  case ":$PATH:" in
    *":$candidate:"*) BIN_DIR="$candidate"; break ;;
  esac
done
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"

if [ ! -d "$CLONE_PATH" ]; then
  echo "cloning $REPO_URL to $CLONE_PATH"
  git clone "$REPO_URL" "$CLONE_PATH"
else
  echo "reusing existing clone at $CLONE_PATH"
fi

DUCKTOOLS_SRC="$CLONE_PATH/ducktools"

mkdir -p "$BIN_DIR"
WRAPPER="$BIN_DIR/ducktools"
cat > "$WRAPPER" <<EOF
#!/usr/bin/env python3
import sys
sys.path.insert(0, "$DUCKTOOLS_SRC/src")
from ducktools.cli import main
main()
EOF
chmod +x "$WRAPPER"
echo "wrapper installed at $WRAPPER"

mkdir -p "$SETTINGS_DIR"
python3 - "$DUCKTOOLS_SRC" "$WRAPPER" <<'PYEOF'
import sys
ducktools_src, wrapper = sys.argv[1], sys.argv[2]
sys.path.insert(0, ducktools_src + "/src")
from ducktools.resolver import _load_settings, _save_settings

settings = _load_settings()
settings['ducktools']['src'] = ducktools_src
settings['ducktools']['bin'] = wrapper
if not settings.get('workspaces'):
    settings['workspaces']['default'] = {'projects': {}}
    settings['active_workspace'] = 'default'
_save_settings(settings)
print(f"settings.json: active workspace = {settings['active_workspace']}, {len(settings['workspaces'])} workspace(s)")
PYEOF

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "warning: $BIN_DIR is not on PATH — add it to your shell profile to use 'ducktools' directly" >&2 ;;
esac

echo "done — run 'ducktools list-workspaces' to verify"
