#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

if [[ "$#" -ne 2 ]]; then
    printf 'Usage: %s <route-file> <route-id>\n' "$0" >&2
    exit 2
fi

# Heal broken inherited conda state
if [[ -n "${CONDA_SHLVL:-}" && "$CONDA_SHLVL" != "0" && -z "${CONDA_PREFIX:-}" ]]; then
    unset CONDA_SHLVL CONDA_DEFAULT_ENV CONDA_PROMPT_MODIFIER
fi

CARLA_ROOT="${CARLA_ROOT:-/home/ip3d/project/CARLA_1.0/Carla-1.0-Linux-Shipping}"
SCENARIO_RUNNER_ROOT="${SCENARIO_RUNNER_ROOT:-$HOME/carla_ws/dependencies/scenario_runner}"
CARLA_AGENTS_ROOT="$CARLA_ROOT/PythonAPI/carla"

if [[ ! -d "$CARLA_AGENTS_ROOT/agents" ]]; then
    printf 'CARLA agents package not found under %s\n' "$CARLA_AGENTS_ROOT" >&2
    printf 'Set CARLA_ROOT to the existing CARLA 1.0 installation.\n' >&2
    exit 2
fi
if [[ ! -d "$SCENARIO_RUNNER_ROOT/srunner" ]]; then
    printf 'ScenarioRunner package not found under %s\n' "$SCENARIO_RUNNER_ROOT" >&2
    printf 'Set SCENARIO_RUNNER_ROOT to the existing ScenarioRunner checkout.\n' >&2
    exit 2
fi

export CARLA_ROOT SCENARIO_RUNNER_ROOT
export LEADERBOARD_ROOT="${LEADERBOARD_ROOT:-$REPO_ROOT}"
export PYTHONPATH="$CARLA_AGENTS_ROOT:$SCENARIO_RUNNER_ROOT:$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

cd "$REPO_ROOT"
exec conda run --no-capture-output -n carla_1.0 python \
    carla_validator/src/carla_validator.py \
    --config carla_validator/config/carla_config.yaml \
    --route-file "$1" \
    --route-id "$2"