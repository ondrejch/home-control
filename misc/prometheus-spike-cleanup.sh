#!/usr/bin/env bash
set -euo pipefail

PROM_URL="http://localhost:9090"
METRIC="pigate_radiation_cpm"
MATCH_EXPR='pigate_radiation_cpm{instance="pigate:9100",job="node_exporters"}'
THRESHOLD="1000"
LOOKBACK="24h"
STEP="30s"
PAD_BEFORE="60"
PAD_AFTER="60"
MERGE_GAP="90"
DRY_RUN="1"
CLEAN_TOMBSTONES="1"

usage() {
  cat <<'USAGE'
Usage:
  prometheus-spike-cleanup.sh [options]

Defaults:
  --prom-url     http://localhost:9090
  --metric       pigate_radiation_cpm
  --match-expr   pigate_radiation_cpm{instance="pigate:9100",job="node_exporters"}
  --threshold    1000
  --lookback     24h
  --step         30s
  --pad-before   60
  --pad-after    60
  --merge-gap    90
  dry-run        enabled by default

Options:
  --prom-url URL
  --metric NAME
  --match-expr EXPR
  --threshold N
  --lookback DUR
  --step DUR
  --pad-before SEC
  --pad-after SEC
  --merge-gap SEC
  --apply        Actually delete data
  --no-clean     Skip clean_tombstones
  -h, --help     Show this help

Examples:
  ./prometheus-spike-cleanup.sh
  ./prometheus-spike-cleanup.sh --apply
  ./prometheus-spike-cleanup.sh --lookback 6h --apply
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prom-url) PROM_URL="$2"; shift 2 ;;
    --metric) METRIC="$2"; shift 2 ;;
    --match-expr) MATCH_EXPR="$2"; shift 2 ;;
    --threshold) THRESHOLD="$2"; shift 2 ;;
    --lookback) LOOKBACK="$2"; shift 2 ;;
    --step) STEP="$2"; shift 2 ;;
    --pad-before) PAD_BEFORE="$2"; shift 2 ;;
    --pad-after) PAD_AFTER="$2"; shift 2 ;;
    --merge-gap) MERGE_GAP="$2"; shift 2 ;;
    --apply) DRY_RUN="0"; shift ;;
    --no-clean) CLEAN_TOMBSTONES="0"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

need_cmd curl
need_cmd jq
need_cmd date

if [[ "$LOOKBACK" =~ ^([0-9]+)([smhdw])$ ]]; then
  n="${BASH_REMATCH[1]}"
  u="${BASH_REMATCH[2]}"
  case "$u" in
    s) delta=$((n)) ;;
    m) delta=$((n * 60)) ;;
    h) delta=$((n * 3600)) ;;
    d) delta=$((n * 86400)) ;;
    w) delta=$((n * 604800)) ;;
  esac
else
  echo "Unsupported --lookback format: $LOOKBACK" >&2
  echo "Use values like 6h, 24h, or 7d" >&2
  exit 1
fi

END_TS=$(date +%s)
START_TS=$((END_TS - delta))
QUERY="${METRIC} > ${THRESHOLD}"

echo "Prometheus: $PROM_URL"
echo "Metric:      $METRIC"
echo "Match expr:  $MATCH_EXPR"
echo "Threshold:   $THRESHOLD"
echo "Lookback:    $LOOKBACK"
echo "Query step:  $STEP"
echo "Time range:  $START_TS -> $END_TS"
echo "Padding:     -${PAD_BEFORE}s / +${PAD_AFTER}s"
echo "Merge gap:   ${MERGE_GAP}s"
echo

RESP=$(curl -fsG "$PROM_URL/api/v1/query_range" \
  --data-urlencode "query=$QUERY" \
  --data-urlencode "start=$START_TS" \
  --data-urlencode "end=$END_TS" \
  --data-urlencode "step=$STEP")

STATUS=$(jq -r '.status' <<<"$RESP")
if [[ "$STATUS" != "success" ]]; then
  echo "Prometheus query failed:" >&2
  echo "$RESP" >&2
  exit 1
fi

mapfile -t SPIKES < <(jq -r '.data.result[]?.values[]?[0]' <<<"$RESP" | sort -n | uniq)

if [[ ${#SPIKES[@]} -eq 0 ]]; then
  echo "No spikes found above threshold."
  exit 0
fi

echo "Spike timestamps:"
printf '  %s\n' "${SPIKES[@]}"
echo

windows=()
cur_start=$(( SPIKES[0] - PAD_BEFORE ))
cur_end=$(( SPIKES[0] + PAD_AFTER ))
prev=${SPIKES[0]}

for ts in "${SPIKES[@]:1}"; do
  if (( ts - prev <= MERGE_GAP )); then
    new_end=$(( ts + PAD_AFTER ))
    if (( new_end > cur_end )); then
      cur_end=$new_end
    fi
  else
    windows+=("${cur_start}:${cur_end}")
    cur_start=$(( ts - PAD_BEFORE ))
    cur_end=$(( ts + PAD_AFTER ))
  fi
  prev=$ts
done
windows+=("${cur_start}:${cur_end}")

echo "Delete windows:"
for w in "${windows[@]}"; do
  s=${w%%:*}
  e=${w##*:}
  echo "  $s -> $e"
done
echo

if [[ "$DRY_RUN" == "1" ]]; then
  echo "Dry run only. Re-run with --apply to delete."
  exit 0
fi

for w in "${windows[@]}"; do
  s=${w%%:*}
  e=${w##*:}
  echo "Deleting $MATCH_EXPR from $s to $e"
  curl -fsS -X POST -g \
    "$PROM_URL/api/v1/admin/tsdb/delete_series?match[]=$MATCH_EXPR&start=$s&end=$e"
  echo
 done

if [[ "$CLEAN_TOMBSTONES" == "1" ]]; then
  echo "Cleaning tombstones..."
  curl -fsS -X POST "$PROM_URL/api/v1/admin/tsdb/clean_tombstones"
  echo
fi

echo "Done. Refresh Grafana or re-run the query_range check to verify the spikes are gone."
