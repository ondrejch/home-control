#!/usr/bin/env bash
set -euo pipefail

PROM_URL="http://localhost:9090"
METRIC="pigate_radiation_cpm"
MATCH_EXPR='pigate_radiation_cpm{instance="pigate:9100",job="node_exporters"}'
THRESHOLD="500"
LOOKBACK="48h"
STEP=""
PAD_BEFORE="60"
PAD_AFTER="60"
MERGE_GAP="90"
DRY_RUN="1"
CLEAN_TOMBSTONES="1"
INFLUX_URL="http://localhost:8086"
INFLUX_DB="prometheus"
CLEAN_INFLUX="1"


usage() {
  cat <<'USAGE'
Usage:
  prometheus-spike-cleanup.sh [options]

Defaults:
  --prom-url     http://localhost:9090
  --metric       pigate_radiation_cpm
  --match-expr   pigate_radiation_cpm{instance="pigate:9100",job="node_exporters"}
  --threshold    500
  --lookback     24h
  --step         adaptive (delta / 10000, min 1s, max 3600s)
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
  --no-clean-influx  Skip InfluxDB deletion
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
    --no-clean-influx) CLEAN_INFLUX="0"; shift ;;
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

# Parse STEP (adaptive if not provided).
MAX_POINTS=10000
if [[ -z "$STEP" ]]; then
  # Default to 10s for fine spike detection, then chunk to stay within MAX_POINTS.
  STEP="10s"
  STEP_VAL=10
  # If lookback is tiny, step can't exceed the full range.
  if (( STEP_VAL > delta )); then STEP="${delta}s"; STEP_VAL=$delta; fi
else
  STEP_VAL=$(echo "$STEP" | sed 's/s$//')
fi

# Chunk the query if total points would exceed MAX_POINTS.
CHUNK_DURATION=$(( STEP_VAL * MAX_POINTS ))
if (( delta > CHUNK_DURATION )); then
  CHUNKS=$(( (delta + CHUNK_DURATION - 1) / CHUNK_DURATION ))
else
  CHUNKS=1
fi

QUERY="${MATCH_EXPR} > ${THRESHOLD}"

echo "Prometheus: $PROM_URL"
echo "Metric:      $METRIC"
echo "Match expr:  $MATCH_EXPR"
echo "Threshold:   $THRESHOLD"
echo "Lookback:    $LOOKBACK"
echo "Query step:  $STEP"
echo "Time range:  $START_TS -> $END_TS"
echo "Padding:     -${PAD_BEFORE}s / +${PAD_AFTER}s"
echo "Merge gap:   ${MERGE_GAP}s"
echo "Chunks:      $CHUNKS"
echo

SPIKES=()
for (( c=0; c<CHUNKS; c++ )); do
  c_start=$(( START_TS + c * CHUNK_DURATION ))
  c_end=$(( START_TS + (c + 1) * CHUNK_DURATION ))
  if (( c_end > END_TS )); then
    c_end=$END_TS
  fi
  echo "Querying chunk $((c+1))/$CHUNKS: $c_start -> $c_end..."
  RESP=$(curl -fsG --max-time 300 "$PROM_URL/api/v1/query_range" \
    --data-urlencode "query=$QUERY" \
    --data-urlencode "start=$c_start" \
    --data-urlencode "end=$c_end" \
    --data-urlencode "step=$STEP")

  STATUS=$(jq -r '.status' <<<"$RESP")
  if [[ "$STATUS" != "success" ]]; then
    echo "Prometheus query failed on chunk $((c+1)):" >&2
    echo "$RESP" >&2
    exit 1
  fi

  mapfile -t CHUNK_SPIKES < <(jq -r '.data.result[]?.values[]?[0]' <<<"$RESP" | sort -n | uniq)
  if [[ ${#CHUNK_SPIKES[@]} -gt 0 ]]; then
    SPIKES+=("${CHUNK_SPIKES[@]}")
  fi
done

if [[ ${#SPIKES[@]} -gt 0 ]]; then
  mapfile -t SPIKES < <(printf '%s\n' "${SPIKES[@]}" | sort -n | uniq)
fi

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

  # Also delete from InfluxDB (remote_read would pull it back)
  if [[ "$CLEAN_INFLUX" == "1" ]]; then
    s_iso=$(date -u -d "@$s" '+%Y-%m-%dT%H:%M:%SZ')
    e_iso=$(date -u -d "@$e" '+%Y-%m-%dT%H:%M:%SZ')
    echo "  InfluxDB: deleting $METRIC from $s_iso to $e_iso"
    RES=$(curl -sG "$INFLUX_URL/query?db=$INFLUX_DB" \
      --data-urlencode "q=DELETE FROM ${METRIC} WHERE time >= '${s_iso}' AND time <= '${e_iso}'")
    if echo "$RES" | grep -q 'error'; then
      echo "  InfluxDB delete failed: $RES" >&2
    fi
  fi
done

if [[ "$CLEAN_TOMBSTONES" == "1" ]]; then
  echo "Cleaning tombstones..."
  curl -fsS -X POST "$PROM_URL/api/v1/admin/tsdb/clean_tombstones"
  echo
fi

echo "Done. Refresh Grafana or re-run the query_range check to verify the spikes are gone."
