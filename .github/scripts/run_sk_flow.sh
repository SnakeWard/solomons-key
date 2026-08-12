#!/usr/bin/env bash
set -euo pipefail

LOG="${LOG_PATH:-sk_flow.log}"
: > "$LOG"
ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

finish() {
  echo "SK_FLOW END: $(ts)" >> "$LOG"
}

fail() {
  local rc="$1"
  shift
  echo "[ERROR] $*" | tee -a "$LOG"
  finish
  exit "$rc"
}

echo "SK_FLOW START: $(ts)" >> "$LOG"
PY="${PYTHON:-python}"

# Find or generate the KEY.
if [ -f key.repaired.yaml ]; then
  KEY=key.repaired.yaml
elif [ -f key.yaml ]; then
  KEY=key.yaml
else
  echo "[INFO] No KEY found; attempting sk_init.py --repo . --out project.key.yaml" | tee -a "$LOG"
  if [ -f sk_init.py ]; then
    if "$PY" sk_init.py --repo . --out project.key.yaml >> "$LOG" 2>&1; then
      INIT_RC=0
    else
      INIT_RC=$?
    fi
    echo "[RESULT] INIT_RC=$INIT_RC" >> "$LOG"
    [ "$INIT_RC" -eq 0 ] || fail "$INIT_RC" "sk_init failed"
    [ -f project.key.yaml ] || fail 3 "sk_init reported success but did not write project.key.yaml"
    KEY=project.key.yaml
  else
    fail 2 "no KEY found and sk_init.py is unavailable"
  fi
fi

echo "[INFO] KEY used: $KEY" >> "$LOG"

# Lint. A missing tool or a nonzero result is a failed flow, never a skip.
[ -f sk_lint.py ] || fail 2 "sk_lint.py is unavailable"
echo "[STEP] sk_lint -> $KEY" | tee -a "$LOG"
if "$PY" sk_lint.py "$KEY" >> "$LOG" 2>&1; then
  LINT_RC=0
else
  LINT_RC=$?
fi
echo "[RESULT] LINT_RC=$LINT_RC" >> "$LOG"
[ "$LINT_RC" -eq 0 ] || fail "$LINT_RC" "sk_lint failed"

# Emit a fresh run. Never reuse an output directory: stale artifacts could make
# an incomplete emission look complete.
LEDGER="${LEDGER_PATH:-ledger/solomons-key-builder-ledger.jsonl}"
OUTDIR="${OUTDIR:-runs/ci_run}"
ROUTE="${ROUTE_ID:-protocol_build_route}"
TRUSTED="${TRUSTED_PROGRAMS_PATH:-TRUSTED_PROGRAMS.sha256}"
SCHEMAS="${SCHEMA_PATH:-schemas/artifacts}"

[ -f "$LEDGER" ] || fail 2 "ledger is unavailable: $LEDGER"
[ -f sk_emit.py ] || fail 2 "sk_emit.py is unavailable"
[ -f sk_verify.py ] || fail 2 "sk_verify.py is unavailable"
[ -f "$TRUSTED" ] || fail 2 "trusted-programs allowlist is unavailable: $TRUSTED"
[ -d "$SCHEMAS" ] || fail 2 "artifact schemas are unavailable: $SCHEMAS"
[ ! -e "$OUTDIR" ] || fail 2 "output path already exists: $OUTDIR"
mkdir -p "$(dirname -- "$OUTDIR")"

echo "[STEP] sk_emit run --key $KEY --route $ROUTE --ledger $LEDGER --out $OUTDIR" | tee -a "$LOG"
if "$PY" sk_emit.py run --key "$KEY" --route "$ROUTE" --ledger "$LEDGER" --out "$OUTDIR" >> "$LOG" 2>&1; then
  EMIT_RC=0
else
  EMIT_RC=$?
fi
echo "[RESULT] EMIT_RC=$EMIT_RC" >> "$LOG"
[ "$EMIT_RC" -eq 0 ] || fail "$EMIT_RC" "sk_emit failed"
[ -f "$OUTDIR/run.json" ] || fail 3 "sk_emit reported success but did not write $OUTDIR/run.json"

# Verify exactly the run that was just emitted, against explicit trust inputs.
echo "[STEP] sk_verify $OUTDIR --key $KEY --schemas $SCHEMAS --ledger $LEDGER --trusted $TRUSTED" | tee -a "$LOG"
if "$PY" sk_verify.py "$OUTDIR" \
  --key "$KEY" \
  --schemas "$SCHEMAS" \
  --ledger "$LEDGER" \
  --trusted "$TRUSTED" >> "$LOG" 2>&1; then
  VERIFY_RC=0
else
  VERIFY_RC=$?
fi
echo "[RESULT] VERIFY_RC=$VERIFY_RC" >> "$LOG"
[ "$VERIFY_RC" -eq 0 ] || fail "$VERIFY_RC" "sk_verify failed"

finish
exit 0
