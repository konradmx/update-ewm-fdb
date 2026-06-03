#!/usr/bin/env bash
set -euo pipefail

# Edit when porting this script to another project.
venv_name='venv_py314_ewm_fdb'

project_root="$(cd "$(dirname "$0")" && pwd)"
venv_base="${VIRTUALENVS_HOME:-$HOME/.virtualenvs}"
venv_path="$venv_base/$venv_name"
venv_link="$project_root/.venv"

mkdir -p "$venv_base"

if [ ! -d "$venv_path" ]; then
    echo "Creating venv at $venv_path"
    uv venv --python 3.14 "$venv_path"
fi

if [ ! -e "$venv_link" ]; then
    echo "Linking $venv_link -> $venv_path"
    ln -s "$venv_path" "$venv_link"
fi

echo "Running uv sync"
uv sync
