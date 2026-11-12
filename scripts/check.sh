#!/usr/bin/env bash
# Gate: runs the checks PR CI runs (.github/workflows/ci.yml jobs backend, ios).
# Usage: scripts/check.sh [--fast] [--part backend|ios]
#   --fast   `make check` only (ruff, offline pytest, Compose validation); no simulator, no database
#   --part   only the checks of that CI job; no args runs every part
# backend DB checks need DATABASE_URL (CI sets it); locally the ios part needs GATE_SIMULATOR_UDID.
# In CI, make uses PYTHON=python RUFF=ruff; locally the Makefile's .venv defaults apply
# unless PYTHON / RUFF are set in the environment.
set -euo pipefail
cd "$(dirname "$0")/.."

fast=0
part=""
while [ $# -gt 0 ]; do
  case "$1" in
    --fast) fast=1 ;;
    --part) part="${2:-}"; [ -n "$part" ] || { echo "--part needs a name" >&2; exit 2; }; shift ;;
    *) echo "usage: scripts/check.sh [--fast] [--part backend|ios]" >&2; exit 2 ;;
  esac
  shift
done
case "$part" in ""|backend|ios) ;; *) echo "unknown part: $part" >&2; exit 2 ;; esac

trap 'rc=$?; [ "$rc" -eq 0 ] || echo "GATE: FAIL"' EXIT

in_ci() { [ "${CI:-}" = "true" ] || [ "${GITHUB_ACTIONS:-}" = "true" ]; }

if in_ci; then
  PYTHON="${PYTHON:-python}"
  RUFF="${RUFF:-ruff}"
fi
make_vars=()
[ -n "${PYTHON:-}" ] && make_vars+=("PYTHON=$PYTHON")
[ -n "${RUFF:-}" ] && make_vars+=("RUFF=$RUFF")

run_make_check() {
  make check ${make_vars[@]+"${make_vars[@]}"}
}

part_backend() {
  if [ -z "${DATABASE_URL:-}" ]; then
    echo "backend needs DATABASE_URL pointing at a disposable pgvector Postgres (CI service container). Never use your local Postgres." >&2
    exit 1
  fi
  run_make_check
  make test-db ${make_vars[@]+"${make_vars[@]}"}
}

part_ios() {
  if in_ci; then
    # Runner images do not always ship the same simulators, so resolve one that
    # is actually installed instead of pinning a device name.
    simulator=$(xcrun simctl list devices available -j | jq -r '
      [.devices | to_entries[]
        | select(.key | contains("iOS"))
        | .value[]
        | select(.name | startswith("iPhone"))][0].udid')
    if [ -z "$simulator" ] || [ "$simulator" = "null" ]; then
      echo "No iPhone simulator is available on this runner image." >&2
      exit 1
    fi
  else
    if [ -z "${GATE_SIMULATOR_UDID:-}" ]; then
      echo "Locally the ios part needs GATE_SIMULATOR_UDID. Create a dedicated \"Audit ...\" simulator and pass its UDID; never use your own simulators." >&2
      exit 1
    fi
    simulator="$GATE_SIMULATOR_UDID"
  fi
  xcodebuild test \
    -project ios/FilingDigest.xcodeproj \
    -scheme FilingDigest \
    -destination "platform=iOS Simulator,id=$simulator" \
    CODE_SIGNING_ALLOWED=NO
}

if [ "$fast" -eq 1 ]; then
  run_make_check
else
  case "$part" in
    backend) part_backend ;;
    ios) part_ios ;;
    "") part_backend; part_ios ;;
  esac
fi

echo "GATE: PASS"
