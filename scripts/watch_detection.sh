#!/usr/bin/env bash
# watch_detection.sh — live view of what the phone is actually detecting, right now.
#
# Answers the one question logs alone cannot: when the user hears nothing, is the pipeline
# broken, or is the camera simply pointed at something with nothing to detect? Those look
# identical from the outside — silence — and that ambiguity is what makes "it stopped working"
# so hard to pin down. This separates them explicitly and continuously.
#
# Usage:
#   ./scripts/watch_detection.sh [device_serial]
#
# Point the rear camera at a PERSON and watch the DETECTING column. Interpretation:
#
#   frames rising + DETECTING person   -> working end to end
#   frames rising + nothing detected   -> pipeline healthy, scene has nothing YOLO recognises
#                                         (a wall, a ceiling, a dim desk — this is NOT a fault)
#   frames NOT rising                  -> the capture pipeline really has stalled: a real fault
#
# Only these classes can ever produce a spoken hazard (backend hazards.py):
#   vehicles : car truck bus motorcycle bicycle auto_rickshaw
#   obstacles: person dog cat chair pole hawker_cart parked_vehicle
# A laptop, tv, bed or cup is detected but is deliberately never announced, so a desk full of
# electronics will look completely silent while being perfectly healthy.

set -uo pipefail

DEVICE="${1:-}"
if [ -z "$DEVICE" ]; then
  DEVICE="$(adb devices | awk 'NR>1 && $2=="device" {print $1}' | head -1)"
fi
[ -n "$DEVICE" ] || { echo "No device. Pass a serial or connect one." >&2; exit 1; }

if [ -t 1 ]; then
  R=$'\033[0m'; DIM=$'\033[2m'; GRN=$'\033[32m'; YEL=$'\033[33m'; RED=$'\033[31m'; BLD=$'\033[1m'
else
  R=""; DIM=""; GRN=""; YEL=""; RED=""; BLD=""
fi

echo "${BLD}Watching $DEVICE — point the rear camera at a person.${R}"
echo "${DIM}Ctrl-C to stop.${R}"
echo
printf "%-9s %8s %8s %8s  %s\n" "time" "frames" "results" "hazards" "detecting"
echo "-------------------------------------------------------------------"

adb -s "$DEVICE" logcat -c 2>/dev/null || true

# Single logcat stream; awk keeps the running tallies and prints one line per second so the
# operator sees a steady heartbeat rather than a burst of noise.
adb -s "$DEVICE" logcat -v time -s AkshravaVision:I AkshravaDebug:I 2>/dev/null | awk -v G="$GRN" -v Y="$YEL" -v RD="$RED" -v D="$DIM" -v N="$R" '
  function flush_line(   status, shown) {
    if (now == last) return
    last = now
    # Stalled capture is the only genuine fault signal here.
    if (frames == prev_frames) { stall++ } else { stall = 0 }
    prev_frames = frames
    if (stall >= 8)      status = RD "CAPTURE STALLED" N
    else if (labels != "") status = G labels N
    else                 status = D "(nothing detectable in view)" N
    printf "%-9s %8d %8d %8d  %s\n", now, frames, results, hazards, status
    labels = ""
  }
  /frame_sent id=/            { frames++ }
  /result_age_ms=/            { results++ }
  /message_key|hazard/        { }
  {
    # Timestamp is field 2 (HH:MM:SS.mmm)
    split($2, t, "."); now = t[1]
  }
  /detections=[1-9]/ {
    # Capture the label list so the operator can see WHAT is being recognised.
    if (match($0, /labels=\[[^]]*\]/)) {
      lbl = substr($0, RSTART+8, RLENGTH-9)
      if (lbl != "") labels = lbl
      # Only these can be spoken; anything else is detected but intentionally silent.
      if (lbl ~ /person|car|truck|bus|motorcycle|bicycle|dog|cat|chair|pole/) hazards++
    }
  }
  { flush_line() }
'
