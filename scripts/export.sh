#!/usr/bin/env bash
set -euo pipefail

BASHRC="${BASHRC:-$HOME/.bashrc}"
START_MARKER="# >>> CARLA Leaderboard environment >>>"
END_MARKER="# <<< CARLA Leaderboard environment <<<"

if [[ ! -f "$BASHRC" ]]; then
    touch "$BASHRC"
fi

TEMP_FILE="$(mktemp "${BASHRC}.XXXXXX")"
trap 'rm -f "$TEMP_FILE"' EXIT

awk -v start="$START_MARKER" -v end="$END_MARKER" '
    $0 == start { inside_block = 1; next }
    $0 == end { inside_block = 0; next }
    !inside_block { print }
' "$BASHRC" > "$TEMP_FILE"

if [[ -s "$TEMP_FILE" ]] && [[ "$(tail -c 1 "$TEMP_FILE" | wc -l)" -eq 0 ]]; then
    printf '\n' >> "$TEMP_FILE"
fi

cat >> "$TEMP_FILE" <<'EOF'
# >>> CARLA Leaderboard environment >>>
export CARLA_ROOT="${CARLA_ROOT:-$HOME/project/CARLA_1.0/Carla-1.0-Linux-Shipping}"
export LEADERBOARD_ROOT="${LEADERBOARD_ROOT:-$HOME/leaderboard}"
export SCENARIO_RUNNER_ROOT="${SCENARIO_RUNNER_ROOT:-$HOME/project/scenario_runner-ue58}"
export PYTHONPATH="$CARLA_ROOT/PythonAPI/carla:$SCENARIO_RUNNER_ROOT:$LEADERBOARD_ROOT${PYTHONPATH:+:$PYTHONPATH}"
# <<< CARLA Leaderboard environment <<<
EOF

chmod --reference="$BASHRC" "$TEMP_FILE" 2>/dev/null || chmod 644 "$TEMP_FILE"
mv "$TEMP_FILE" "$BASHRC"
trap - EXIT

printf 'CARLA Leaderboard exports added to %s.\n' "$BASHRC"
printf 'Run "source %s" in the current shell or open a new shell to apply them.\n' "$BASHRC"


