#!/usr/bin/env bash
#
# Install Cauth from a checkout or a remotely streamed copy of this script.
#
# BUILD-HANDOFF.md called for "pip install + symlink to ~/.local/bin". A bare symlink to
# cauth.py cannot work on this machine: the shebang resolves to the system interpreter,
# which has no Textual, and Arch marks its Python externally managed so `pip install`
# into it is refused. So this creates a private venv and writes a small wrapper that
# execs that venv's python against the repo checkout. See docs/adr/0001 for the decision.
#
# When this file is piped to Bash, it clones the repository into a stable managed checkout
# first. When it is run from a checkout, it installs that checkout directly. Both paths are
# idempotent. See docs/adr/0006 for the remote-install decision.

set -euo pipefail

REPO_URL="${CAUTH_REPO_URL:-}"
APP_DIR="${CAUTH_APP_DIR:-$HOME/.local/share/claude-cauth/app}"
VENV_DIR="${CAUTH_VENV:-$HOME/.local/share/claude-cauth/venv}"
BIN_DIR="${CAUTH_BIN:-$HOME/.local/bin}"
LAUNCHER="$BIN_DIR/cauth"

say() { printf '  %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

normalize_repo_url() {
  normalized=${1%/}
  normalized=${normalized%.git}
  printf '%s\n' "$normalized"
}

bootstrap_remote_checkout() {
  [ -n "$REPO_URL" ] || die "Set CAUTH_REPO_URL to the Git repository URL for a remote install, or run install.sh from a checkout"
  command -v git >/dev/null 2>&1 || die "git is required for a remote install"
  case "$APP_DIR" in
    ""|/|"$HOME") die "CAUTH_APP_DIR must name a dedicated application directory" ;;
  esac

  say "remote  $REPO_URL"
  say "app     $APP_DIR"

  if [ -e "$APP_DIR" ] && [ ! -d "$APP_DIR/.git" ]; then
    die "$APP_DIR already exists and is not a Git checkout"
  fi

  if [ -d "$APP_DIR/.git" ]; then
    current_origin=$(git -C "$APP_DIR" remote get-url origin 2>/dev/null) ||
      die "$APP_DIR has no readable origin remote"
    if [ "$(normalize_repo_url "$current_origin")" != "$(normalize_repo_url "$REPO_URL")" ]; then
      die "$APP_DIR belongs to a different origin: $current_origin"
    fi

    current_branch=$(git -C "$APP_DIR" symbolic-ref --quiet --short HEAD 2>/dev/null || true)
    [ "$current_branch" = "main" ] ||
      die "$APP_DIR must be on main, not ${current_branch:-a detached HEAD}"
    [ -z "$(git -C "$APP_DIR" status --porcelain)" ] ||
      die "$APP_DIR has local changes; refusing to overwrite them"

    say "updating managed checkout"
    git -C "$APP_DIR" fetch --quiet origin main:refs/remotes/origin/main
    git -C "$APP_DIR" merge --ff-only --quiet origin/main
  else
    mkdir -p "$(dirname "$APP_DIR")"
    say "cloning managed checkout"
    git clone --quiet --depth 1 --branch main --single-branch "$REPO_URL" "$APP_DIR"
  fi

  exec bash "$APP_DIR/install.sh"
}

SCRIPT_SOURCE="${BASH_SOURCE[0]:-}"
SCRIPT_DIR=""
if [ -n "$SCRIPT_SOURCE" ] && [ -f "$SCRIPT_SOURCE" ]; then
  SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_SOURCE")" && pwd)"
fi

if [ -z "$SCRIPT_DIR" ] || [ ! -f "$SCRIPT_DIR/cauth.py" ] ||
   [ ! -f "$SCRIPT_DIR/requirements.txt" ]; then
  bootstrap_remote_checkout
fi

REPO_DIR="$SCRIPT_DIR"

say "repo    $REPO_DIR"
say "venv    $VENV_DIR"
say "command $LAUNCHER"
echo

# ---------------------------------------------------------------------------- venv
# `python3 -m venv --help` succeeding proves nothing: on Debian and Ubuntu without
# the python3-venv package the module is importable but ensurepip is not, so venv
# creation either fails outright or leaves a tree with no pip in it. A stale
# directory from some other tool lands in the same place. Either way the old
# "python exists, reuse it" check handed the install step a venv it could not
# install into, so probe for a usable installer instead of trusting the path.
have_uv()  { command -v uv >/dev/null 2>&1; }
venv_pip() { [ -x "$VENV_DIR/bin/python" ] && "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1; }
sys_pip()  { python3 -m pip --version >/dev/null 2>&1; }

venv_usable() {
  if [ ! -x "$VENV_DIR/bin/python" ]; then return 1; fi
  if have_uv; then return 0; fi
  if venv_pip; then return 0; fi
  # A pip-less venv is still fine when the system pip can install into it.
  sys_pip
}

if venv_usable; then
  say "venv already present, reusing it"
else
  if [ -e "$VENV_DIR" ]; then
    say "venv present but has no usable installer, rebuilding it"
    rm -rf "$VENV_DIR"
  fi
  say "creating venv"
  if have_uv; then
    uv venv "$VENV_DIR" >/dev/null
  elif python3 -m venv "$VENV_DIR" >/dev/null 2>&1; then
    :
  elif python3 -m venv --without-pip "$VENV_DIR" >/dev/null 2>&1; then
    say "no ensurepip on this host, built the venv bare for the system pip to fill"
  else
    echo "error: need uv, or python3-venv, to build the environment" >&2
    exit 1
  fi
fi

say "installing dependencies"
if have_uv; then
  uv pip install --python "$VENV_DIR/bin/python" -r "$REPO_DIR/requirements.txt" >/dev/null
elif venv_pip; then
  "$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip
  "$VENV_DIR/bin/python" -m pip install --quiet -r "$REPO_DIR/requirements.txt"
elif sys_pip; then
  # System pip installing into another interpreter. --python has to come before
  # the subcommand, and pip 22.3 is the first release that accepts it.
  python3 -m pip --python "$VENV_DIR/bin/python" install --quiet -r "$REPO_DIR/requirements.txt"
else
  echo "error: no pip in the venv and none on the system" >&2
  exit 1
fi

# ---------------------------------------------------------------------------- launcher
mkdir -p "$BIN_DIR"
cat > "$LAUNCHER" <<WRAPPER
#!/usr/bin/env bash
# Generated by claude-cauth install.sh. Re-run that script to regenerate.
exec "$VENV_DIR/bin/python" "$REPO_DIR/cauth.py" "\$@"
WRAPPER
chmod 755 "$LAUNCHER"

# ---------------------------------------------------------------------------- verify
echo
# --version, deliberately not `status`: status calls ensure_store, which would
# CREATE an empty account index as a side effect of merely verifying the
# install. On a fresh machine that empty index (mtime now) then wins any
# newer-file-wins sync against a real index arriving from another machine.
# Verification must be read-only.
if ! "$LAUNCHER" --version >/dev/null 2>&1; then
  echo "error: '$LAUNCHER --version' did not run cleanly" >&2
  exit 1
fi
say "verified: cauth --version runs"
if ! "$VENV_DIR/bin/python" -B -c 'import sys; assert sys.version_info >= (3, 9), "Python 3.9+ required"; sys.path.insert(0, sys.argv[1]); import tui, tokenui, tokenauth' "$REPO_DIR"; then
  die "the terminal UI could not import; repair this environment and rerun install.sh"
fi
say "verified: terminal UI imports"
case "${SHELL:-}" in
  */bash) "$VENV_DIR/bin/python" -B "$REPO_DIR/cauth.py" install-shell bash ;;
  */zsh) "$VENV_DIR/bin/python" -B "$REPO_DIR/cauth.py" install-shell zsh ;;
  *) say "Use cauth run to launch Claude, or cauth shell-init for shell integration." ;;
esac
if ! command -v claude >/dev/null 2>&1; then
  say "NEXT: install Claude Code before signing in or checking accounts. Run cauth doctor for details."
fi

if command -v cauth >/dev/null 2>&1 && [ "$(command -v cauth)" = "$LAUNCHER" ]; then
  say "verified: cauth resolves on PATH"
else
  echo
  if command -v cauth >/dev/null 2>&1; then
    say "NOTE: another cauth command shadows this installation: $(command -v cauth)"
  else
    say "NOTE: $BIN_DIR is not on your PATH yet."
  fi
  say "For Bash, put this directory first (then open a new terminal):"
  printf '    export PATH=%q:"$PATH"\n' "$BIN_DIR"
fi

echo
say "done. run: cauth"
