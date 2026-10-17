#!/usr/bin/env bash
# Classic installer for @Duckspec/@DuckTools — clones the framework once and
# wires up a dependency-free `ducktools` binary + the shared settings registry.
# No pip, no venv: @DuckToolsApp is pure stdlib, so a tiny wrapper script is enough.
# Runs on Linux, macOS and Windows under Git Bash.
set -euo pipefail

if [ $# -lt 1 ] || [ $# -gt 2 ]; then
  echo "usage: install.sh <path-to-clone-duckspec-into> [bin-dir]" >&2
  exit 1
fi

CLONE_PATH="$1"
REPO_URL="https://github.com/komorebinator/duckspec.git"

# Windows installs Python as `python`, and its `python3` may be a stub that only opens the
# Store, so each name is tried by running it rather than by its presence.
PYTHON=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 \
     && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
    PYTHON="$(command -v "$candidate")"
    break
  fi
done
if [ -z "$PYTHON" ]; then
  echo "error: Python 3.10 or newer is required, as python3 or python" >&2
  exit 1
fi

# Under Git Bash or Cygwin the Python that reads recorded paths is a native Windows program,
# which cannot open /c/... — paths are converted to C:/... wherever cygpath exists.
native() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s\n' "$1"; fi
}

# A directory under $HOME can be created by the user; anything else must already be writable,
# so a system directory such as /usr/local/bin is skipped for a non-root user.
usable() {
  case "$1" in
    "$HOME"/*) [ ! -e "$1" ] || [ -w "$1" ] ;;
    *) [ -d "$1" ] && [ -w "$1" ] ;;
  esac
}

# The same candidates @Duckspec#install offers the user: Linux's ~/.local/bin, Git Bash's ~/bin,
# Homebrew's /opt/homebrew/bin or /usr/local/bin on macOS — no OS detection needed, just ask PATH
# what it actually uses.
BIN_DIR="${2:-}"
if [ -z "$BIN_DIR" ]; then
  for candidate in "$HOME/.local/bin" "$HOME/bin" "/opt/homebrew/bin" "/usr/local/bin"; do
    case ":$PATH:" in
      *":$candidate:"*) if usable "$candidate"; then BIN_DIR="$candidate"; break; fi ;;
    esac
  done
  BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
fi

# A user asked for a folder easily names the one that will hold the clone; installing into that
# would leave a wrapper pointing at nothing.
if [ ! -e "$CLONE_PATH" ]; then
  echo "cloning $REPO_URL to $CLONE_PATH"
  git clone "$REPO_URL" "$CLONE_PATH"
elif [ -f "$CLONE_PATH/Duckspec.yaml" ]; then
  echo "reusing existing clone at $CLONE_PATH"
else
  echo "error: $CLONE_PATH already exists and is not a duckspec clone — give the path of the clone itself, e.g. $CLONE_PATH/duckspec" >&2
  exit 1
fi

# Both the wrapper and settings.json are used from whatever directory ducktools later runs in,
# where a relative path points at nothing.
CLONE_PATH="$(native "$(cd "$CLONE_PATH" && pwd)")"
DUCKTOOLS_SRC="$CLONE_PATH/ducktools"

# The binary behind whatever name was found: a version manager's shim (pyenv, asdf, mise) is
# itself a script, and on Windows this is the python.exe cmd and an MCP client need.
PYTHON_BIN="$("$PYTHON" -c 'import sys; print(sys.executable)' | tr -d '\r')"

# The shebang names the interpreter by absolute path, so PATH at run time cannot swap in another —
# macOS's own python3 is 3.9. macOS will not run a script as an interpreter, so a shim is replaced
# by its binary; and a shebang cannot carry a space (C:\Program Files), so that falls back to env.
if command -v cygpath >/dev/null 2>&1; then
  SHEBANG="$(cygpath -u "$PYTHON_BIN")"
elif [ "$(head -c 2 "$PYTHON")" = '#!' ]; then
  SHEBANG="$PYTHON_BIN"
else
  SHEBANG="$PYTHON"
fi
case "$SHEBANG" in *" "*) SHEBANG="/usr/bin/env $(basename "$PYTHON")" ;; esac

mkdir -p "$BIN_DIR"
BIN_DIR="$(cd "$BIN_DIR" && pwd)"
WRAPPER="$BIN_DIR/ducktools"
cat > "$WRAPPER" <<EOF
#!$SHEBANG
import sys
sys.path.insert(0, "$DUCKTOOLS_SRC/src")
from ducktools.cli import main
main()
EOF
chmod +x "$WRAPPER"
echo "wrapper installed at $WRAPPER"

# cmd and PowerShell ignore shebangs, so on Windows the wrapper also gets a .cmd shim. cmd reads it
# in the console's legacy code page, so the user's profile — where Python usually lives, under a
# name that may not be ASCII — is written as %USERPROFILE% rather than spelled out.
if command -v cygpath >/dev/null 2>&1; then
  WIN_PYTHON="$(cygpath -w "$PYTHON_BIN")"
  if [ -n "${USERPROFILE:-}" ]; then
    case "$WIN_PYTHON" in
      "$USERPROFILE"\\*) WIN_PYTHON="%USERPROFILE%${WIN_PYTHON#"$USERPROFILE"}" ;;
    esac
  fi
  printf '@"%s" "%%~dp0ducktools" %%*\r\n' "$WIN_PYTHON" > "$WRAPPER.cmd"
  echo "Windows shim installed at $WRAPPER.cmd"
fi

"$PYTHON" - "$DUCKTOOLS_SRC" "$(native "$WRAPPER")" "$(native "$PYTHON_BIN")" <<'PYEOF'
import sys
# The console's legacy code page on Windows cannot print every user name in these paths
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
ducktools_src, wrapper, python = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, ducktools_src + "/src")
from ducktools.resolver import _load_settings, _save_settings, _register_framework

settings = _load_settings()
settings['ducktools']['src'] = ducktools_src
settings['ducktools']['bin'] = wrapper
settings['ducktools']['python'] = python
if not settings.get('workspaces'):
    settings['workspaces']['default'] = {'projects': {}}
    settings['active_workspace'] = 'default'
_save_settings(settings)
print(f"settings.json: active workspace = {settings['active_workspace']}, {len(settings['workspaces'])} workspace(s)")

registered = _register_framework()
if registered is None:
    print("warning: duckspec could not be registered — no active workspace", file=sys.stderr)
else:
    repository, path, added = registered
    print(f"duckspec {'registered' if added else 'already registered'} at {path}")
PYEOF

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "warning: $BIN_DIR is not on PATH — add it to your shell profile to use 'ducktools' directly" >&2 ;;
esac

echo "done — run 'ducktools list-projects' to verify"
