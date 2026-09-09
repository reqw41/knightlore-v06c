#!/bin/bash
# Сборка Knight Lore для Вектора.
#   ./build.sh lockstep  — i8080, логика 1:1, вывод заглушен, гейт check_i8080 + локстеп (tools/lockstep_vec.js)
#   ./build.sh z80vec    — Z80 для Вектора с Z80-адаптером, ч/б: загрузчик, экран, клавиатура, ВИ53
set -e
cd "$(dirname "$0")"
TARGET=${1:-lockstep}
mkdir -p build
if [ "$TARGET" = "z80vec" ]; then
  python3 tools/kl_z80_to_8080.py src/knightlore_z80.asm build/knightlore_z80.asm --target z80vec
  ./tools/sjasmplus --lst=build/knightlore_z80.lst --sld=build/knightlore_z80.sld --fullpath build/knightlore_z80.asm
  ls -la build/knightlore_z80.r0m
else
  python3 tools/kl_z80_to_8080.py src/knightlore_z80.asm build/knightlore_8080.asm --target $TARGET
  ./tools/sjasmplus --lst=build/knightlore_8080.lst --sld=build/knightlore_8080.sld --fullpath build/knightlore_8080.asm
  python3 tools/check_i8080.py build/knightlore_8080.lst
  ls -la build/knightlore_8080.r0m
fi
