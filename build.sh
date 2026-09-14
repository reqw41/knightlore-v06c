#!/bin/bash
# Сборка Knight Lore для Вектора.
#   ./build.sh lockstep  — i8080, логика 1:1, вывод заглушен, гейт check_i8080 + локстеп (tools/lockstep_vec.js)
#   ./build.sh z80vec    — Z80 для Вектора с Z80-адаптером, ч/б: загрузчик, экран, клавиатура, ВИ53
set -e
cd "$(dirname "$0")"
TARGET=${1:-lockstep}
mkdir -p build
if [ "$TARGET" = "jukebox" ] || [ -n "$KL_JUKEBOX" ]; then
  # тот же образ, но стартовый экран — стенд приёмки звука на слух: 1..9/A..H запускают
  # по одному каждый эффект и каждую мелодию (таблица в docs/PORT_RECIPE.md §23).
  python3 tools/kl_z80_to_8080.py src/knightlore_z80.asm build/knightlore_jukebox.asm --target vec --jukebox
  ./tools/sjasmplus --lst=build/knightlore_jukebox.lst --sld=build/knightlore_jukebox.sld --fullpath build/knightlore_jukebox.asm
  python3 tools/check_i8080.py build/knightlore_jukebox.lst
  python3 tools/check_ixflags.py build/knightlore_jukebox.asm
  python3 tools/check_plane2.py build/knightlore_jukebox.lst
  ls -la build/knightlore_jukebox.r0m
elif [ "$TARGET" = "vec" ]; then
  python3 tools/kl_z80_to_8080.py src/knightlore_z80.asm build/knightlore_vec.asm --target vec
  ./tools/sjasmplus --lst=build/knightlore_vec.lst --sld=build/knightlore_vec.sld --fullpath build/knightlore_vec.asm
  python3 tools/check_i8080.py build/knightlore_vec.lst
  python3 tools/check_ixflags.py build/knightlore_vec.asm
  python3 tools/check_plane2.py build/knightlore_vec.lst
  ls -la build/knightlore_vec.r0m
elif [ "$TARGET" = "z80vec" ]; then
  python3 tools/kl_z80_to_8080.py src/knightlore_z80.asm build/knightlore_z80.asm --target z80vec
  ./tools/sjasmplus --lst=build/knightlore_z80.lst --sld=build/knightlore_z80.sld --fullpath build/knightlore_z80.asm
  ls -la build/knightlore_z80.r0m
else
  python3 tools/kl_z80_to_8080.py src/knightlore_z80.asm build/knightlore_8080.asm --target $TARGET
  ./tools/sjasmplus --lst=build/knightlore_8080.lst --sld=build/knightlore_8080.sld --fullpath build/knightlore_8080.asm
  python3 tools/check_i8080.py build/knightlore_8080.lst
  python3 tools/check_ixflags.py build/knightlore_8080.asm
  ls -la build/knightlore_8080.r0m
fi
