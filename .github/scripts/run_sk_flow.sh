#!/usr/bin/env bash
set -euo pipefail

LOG=sk_flow.log
: > "$LOG"
ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

echo "SK_FLOW START: $(ts)" >> "$LOG"
PY=python

# find or generate KEY
if [ -f key.repaired.yaml ]; then
  KEY=key.repaired.yaml
elif [ -f key.yaml ]; then
  KEY=key.yaml
else
  echo "[INFO] No KEY found; attempting sk_init.py --repo . --out project.key.yaml" | tee -a "$LOG"
  if [ -f sk_init.py ]; then
    $PY sk_init.py --repo . --out project.key.yaml >> "$LOG" 2>&1 || { echo "[ERROR] sk_init failed" | tee -a "$LOG"; exit 2; }
    KEY=project.key.yaml
  else
    echo "[WARN] sk_init.py not present; cannot derive KEY" | tee -a "$LOG"
    KEY=""
  fi
fi

echo "[INFO] KEY used: ${KEY:-<none>}" >> "$LOG"

# LINT (if key exists)
LINT_RC=0
if [ -n "$KEY" ] && [ -f "$KEY" ]; then
  echo "[STEP] sk_lint -> $KEY" | tee -a "$LOG"
  $PY sk_lint.py "$KEY" >> "$LOG" 2>&1 || LINT_RC=$?
else
  echo "[SKIP] sk_lint (no KEY available)" | tee -a "$LOG"
fi
echo "[RESULT] LINT_RC=$LINT_RC" >> "$LOG"

# Emit run if ledger exists; otherwise skip emit and try to verify runs/good or existing run
EMIT_RC=0
LEDGER="${LEDGER_PATH:-ledger/ledger.json}"
OUTDIR="${OUTDIR:-runs/ci_run}"
ROUTE="${ROUTE_ID:-task.solomons-key.v1.build}"
mkdir -p "$OUTDIR"

if [ -f "$LEDGER" ] && [ -f sk_emit.py ]; then
  echo "[STEP] sk_emit run --key $KEY --route $ROUTE --ledger $LEDGER --out $OUTDIR" | tee -a "$LOG"
  $PY sk_emit.py run --key "$KEY" --route "$ROUTE" --ledger "$LEDGER" --out "$OUTDIR" >> "$LOG" 2>&1 || EMIT_RC=$?
else
  echo "[SKIP] sk_emit run (ledger missing or sk_emit.py not present)" | tee -a "$LOG"
fi
echo "[RESULT] EMIT_RC=$EMIT_RC" >> "$LOG"

# VERIFY
VERIFY_RC=2
if [ -f "$OUTDIR/run.json" ] && [ -f sk_verify.py ]; then
  echo "[STEP] sk_verify $OUTDIR --key $KEY --trusted TRUSTED_PROGRAMS.sha256" | tee -a "$LOG"
  $PY sk_verify.py "$OUTDIR" --key "$KEY" --trusted TRUSTED_PROGRAMS.sha256 >> "$LOG" 2>&1 || VERIFY_RC=$?
elif [ -d "runs/good" ] && [ -f sk_verify.py ]; then
  echo "[STEP] sk_verify runs/good --key $KEY --trusted TRUSTED_PROGRAMS.sha256" | tee -a "$LOG"
  $PY sk_verify.py runs/good --key "$KEY" --trusted TRUSTED_PROGRAMS.sha256 >> "$LOG" 2>&1 || VERIFY_RC=$?
else
  echo "[SKIP] sk_verify: no run directory found or sk_verify.py missing" | tee -a "$LOG"
fi
echo "[RESULT] VERIFY_RC=${VERIFY_RC:-unknown}" >> "$LOG"

echo "SK_FLOW END: $(ts)" >> "$LOG"
# exit with verify rc if present, else non-zero to indicate skip
if [ "${VERIFY_RC:-2}" -eq 0 ]; then
  exit 0
else
  exit 1
fi
