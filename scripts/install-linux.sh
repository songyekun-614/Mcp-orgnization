#!/bin/sh
set -eu
source_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
environment=${MCP_MANAGER_VENV:-"$HOME/venvs/mcp-manager"}
python3 -m venv "$environment"
"$environment/bin/python" -m pip install "$source_root[gui]"
"$environment/bin/python" "$source_root/scripts/install_user_entry.py" --python "$environment/bin/python" --desktop
