#!/bin/sh
# Запуск сборки в Emu80 (порт MCP 19785, чтобы не делить 19781 с чужими).
#   tools/emu80_run.sh z80   — build/knightlore_z80.r0m на платформе vector.z80 (Z80-адаптер)
#   tools/emu80_run.sh 8080  — build/knightlore_8080.r0m на платформе vector (i8080)
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP=~/projects/emu80v4/Emu80qt-mcp.app/Contents/MacOS
TMP="${TMPDIR:-/tmp}/kl_emu80"; mkdir -p "$TMP"
pkill -f "Emu80qt-mcp.*knightlore" 2>/dev/null || true
if [ "$1" = "8080" ]; then PLAT=vector; IMG=knightlore_vec.r0m; else PLAT=vector.z80; IMG=knightlore_z80.r0m; fi
cp "$ROOT/build/$IMG" "$TMP/$IMG"
cd "$APP"
QT_PLUGIN_PATH=/opt/homebrew/share/qt/plugins EMU80_MCP_PORT=19785 nohup ./Emu80qt-mcp --platform $PLAT --run "$TMP/$IMG" >"$TMP/emu80.log" 2>&1 &
echo "Emu80 запущен: $PLAT $IMG (MCP порт 19785)"
