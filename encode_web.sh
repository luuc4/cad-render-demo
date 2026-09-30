#!/usr/bin/env bash
# encode_web.sh – PNG-Sequenz -> Web-Loop (WebM VP9 + MP4 H.264) + Poster-Bild
#
# Aufruf:  ./encode_web.sh [frames-ordner] [ausgabe-ordner] [dateiname]
#   ./encode_web.sh                                                     # finaler Turntable
#   ./encode_web.sh renders/turntable/preview/frames renders/turntable/preview/web
#   ./encode_web.sh renders/assembly/frames renders/assembly/web radial-engine-assembly
set -euo pipefail
cd "$(dirname "$0")"

# ── Parameter ────────────────────────────────────────────────────────────────
IN_DIR="${1:-renders/turntable/frames}"   # Ordner mit frame_0001.png …
OUT_DIR="${2:-renders/turntable/web}"
NAME="${3:-radial-engine}"                # Dateiname ohne Endung
FPS=30
WEBM_CRF=34                       # VP9: 15–50, höher = kleiner/schlechter (31–36 ist für Web üblich)
MP4_CRF=23                        # H.264: 18–28, höher = kleiner/schlechter
MAX_MB=5                          # Warnung, wenn eine Datei größer wird
POSTER_QUALITY=3                  # JPEG 2–31, kleiner = besser
# ─────────────────────────────────────────────────────────────────────────────

mkdir -p "$OUT_DIR"
INPUT=(-framerate "$FPS" -i "$IN_DIR/frame_%04d.png")
# sRGB-PNG -> BT.709 Limited Range, sauber getaggt: Browser dekodieren Weiß wieder exakt zu #FFFFFF
COLOR=(-vf "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p"
       -color_primaries bt709 -color_trc bt709 -colorspace bt709 -color_range tv)

echo "▸ WebM (VP9, 2 Durchgänge) …"
PASSLOG="$(mktemp -d)/vp9"
ffmpeg -v error -y "${INPUT[@]}" "${COLOR[@]}" -c:v libvpx-vp9 -b:v 0 -crf "$WEBM_CRF" \
  -row-mt 1 -deadline good -cpu-used 1 -pass 1 -passlogfile "$PASSLOG" -an -f null /dev/null
ffmpeg -v error -y "${INPUT[@]}" "${COLOR[@]}" -c:v libvpx-vp9 -b:v 0 -crf "$WEBM_CRF" \
  -row-mt 1 -deadline good -cpu-used 1 -pass 2 -passlogfile "$PASSLOG" -an "$OUT_DIR/$NAME.webm"

echo "▸ MP4 (H.264) …"
ffmpeg -v error -y "${INPUT[@]}" "${COLOR[@]}" -c:v libx264 -preset slow -crf "$MP4_CRF" \
  -profile:v high -movflags +faststart -an "$OUT_DIR/$NAME.mp4"

echo "▸ Poster (erster Frame) …"
ffmpeg -v error -y -i "$IN_DIR/frame_0001.png" -q:v "$POSTER_QUALITY" "$OUT_DIR/$NAME-poster.jpg"

echo
for f in "$OUT_DIR/$NAME.webm" "$OUT_DIR/$NAME.mp4" "$OUT_DIR/$NAME-poster.jpg"; do
  bytes=$(stat -f%z "$f")
  mb=$(echo "scale=2; $bytes/1048576" | bc)
  flag=""; (( bytes > MAX_MB * 1048576 )) && flag="  ⚠ größer als ${MAX_MB} MB"
  printf "  %-40s %6s MB%s\n" "$f" "$mb" "$flag"
done
