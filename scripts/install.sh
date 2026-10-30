#!/usr/bin/env bash
# Install the flood forecast on this computer, once (Linux or macOS):
#
#   git clone https://github.com/SauravBhattarai19/lynch-forecast.git
#   cd lynch-forecast
#   scripts/install.sh                 # or: scripts/install.sh --python /path/to/python3.11
#
# It makes a Python environment in .venv, installs this package with the MRRpy
# model it needs, checks the computer ('lynch-forecast doctor') and says what
# is left to do. Running it again updates the installation.
set -euo pipefail
cd "$(dirname "$0")/.."

python=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --python) python="$2"; shift 2 ;;
        -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
        *) echo "Unknown option '$1' (see --help)." >&2; exit 1 ;;
    esac
done

if [[ -z "$python" ]]; then
    for p in python3.12 python3.11 python3.10 python3; do
        if command -v "$p" >/dev/null 2>&1 && \
           "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
            python="$(command -v "$p")"; break
        fi
    done
fi
if [[ -z "$python" ]]; then
    echo "Python 3.10 or newer is needed. On Ubuntu: sudo apt install python3 python3-venv" >&2
    exit 1
fi
command -v git >/dev/null 2>&1 || { echo "git is needed. On Ubuntu: sudo apt install git" >&2; exit 1; }

echo "Using $python ($("$python" --version 2>&1))"
if [[ ! -x .venv/bin/python ]]; then
    "$python" -m venv .venv || {
        echo "Could not make the environment. On Ubuntu: sudo apt install python3-venv" >&2
        exit 1; }
fi
.venv/bin/python -m pip install --quiet --upgrade pip
echo "Installing the forecast and the MRRpy model (a few minutes the first time) …"
.venv/bin/python -m pip install --quiet --upgrade -e ".[setup]"

echo
.venv/bin/lynch-forecast --version
.venv/bin/lynch-forecast doctor || true

cat <<EOF

Installed in $(pwd)/.venv. What is left (once):
  1. Earth Engine, to set up places (DEM, soil and land-cover layers):
       .venv/bin/MRRpy earth-engine-login --project YOUR-EARTH-ENGINE-PROJECT
  2. Station passwords, if a station needs one (e.g. RTS):
       scripts/save-credentials.sh rts
  3. Publishing: this computer must be able to 'git push' to the repository
     named under publish: in places.yaml (an SSH key on GitHub, or 'gh auth login').
  4. Start it for good (it then runs by itself, also after a restart):
       .venv/bin/lynch-forecast service install
Check on it any time with:  .venv/bin/lynch-forecast doctor
The operator guide is docs/operator-guide.md.
EOF
