#!/bin/bash
# Сборка Knight Lore для Вектора.
#   ./build.sh lockstep  — i8080, логика 1:1, вывод заглушен, гейт check_i8080 + локстеп (tools/lockstep_vec.js)
#   ./build.sh z80vec    — Z80 для Вектора с Z80-адаптером, ч/б: загрузчик, экран, клавиатура, ВИ53
#   ./build.sh release   — РЕЛИЗНЫЙ образ: 16-цветная заставка + та же игра, упакованная ZX0 (§33)
#   ./build.sh roms      — release и vec, копии в rom/knightlore.c00 и rom/knightlore-notitle.c00
#                          (образ памяти с 0x0000: на MiSTer — файл .C00, в эмуляторах — .r0m)
#                          и rom/knightlore-0100.rom — тот же снимок без заставки, упакованный
#                          в обычный образ с 0x0100 (tools/mkcom.py; Турбо+ из CP/M: -V T3)
# Ассемблер — tools/sjasmplus (сборка под macOS arm64) или путь в SJASMPLUS.
set -e
cd "$(dirname "$0")"
SJ=${SJASMPLUS:-./tools/sjasmplus}
TARGET=${1:-lockstep}
if [ "$TARGET" = "roms" ]; then
  "$0" release
  mkdir -p rom
  cp build/knightlore_title.r0m rom/knightlore.c00
  cp build/knightlore_vec.r0m rom/knightlore-notitle.c00
  # образ с 0x0100 для загрузчиков, которые кладут программу с 0x0100 (обычный .rom Вектора,
  # Вектор Турбо+ из CP/M с ключом -V): тот же снимок без заставки, упакованный tools/mkcom.py
  python3 tools/mkcom.py build/knightlore_vec.r0m build/knightlore_com.asm
  case "$SJ" in /*) SJA="$SJ" ;; */*) SJA="$PWD/$SJ" ;; *) SJA="$SJ" ;; esac
  (cd build && "$SJA" --nologo --msg=war --i8080 knightlore_com.asm --lst=knightlore_com.lst)
  cp build/knightlore_com.rom rom/knightlore-0100.rom
  V06=${V06JS:-${V06:-$HOME/projects/vector06js}}
  if [ -d "$V06" ]; then V06JS="$V06" node tools/comgate_vec.js rom/knightlore-0100.rom rom/knightlore-notitle.c00
  else echo "comgate пропущен: нет vector06js ($V06)"; fi
  ls -l rom/
  exit 0
fi
mkdir -p build
if [ "$TARGET" = "jukebox" ] || [ -n "$KL_JUKEBOX" ]; then
  # тот же образ, но стартовый экран — стенд приёмки звука на слух: 1..9/A..H запускают
  # по одному каждый эффект и каждую мелодию (таблица в docs/PORT_RECIPE.md §23).
  python3 tools/kl_z80_to_8080.py src/knightlore_z80.asm build/knightlore_jukebox.asm --target vec --jukebox
  "$SJ" --lst=build/knightlore_jukebox.lst --sld=build/knightlore_jukebox.sld --fullpath build/knightlore_jukebox.asm
  python3 tools/check_i8080.py build/knightlore_jukebox.lst
  python3 tools/check_ixflags.py build/knightlore_jukebox.asm
  python3 tools/check_plane2.py build/knightlore_jukebox.lst
  ls -la build/knightlore_jukebox.r0m
elif [ "$TARGET" = "vec" ]; then
  python3 tools/kl_z80_to_8080.py src/knightlore_z80.asm build/knightlore_vec.asm --target vec
  "$SJ" --lst=build/knightlore_vec.lst --sld=build/knightlore_vec.sld --fullpath build/knightlore_vec.asm
  python3 tools/check_i8080.py build/knightlore_vec.lst
  python3 tools/check_ixflags.py build/knightlore_vec.asm
  python3 tools/check_plane2.py build/knightlore_vec.lst
  ls -la build/knightlore_vec.r0m
elif [ "$TARGET" = "release" ]; then
  # Релиз = цель vec, упакованная под заставку. Игру НЕ пересобираем иначе: образ vec
  # собирается ровно так же, как обычно, и его содержимое уходит в поток целиком.
  python3 tools/kl_z80_to_8080.py src/knightlore_z80.asm build/knightlore_vec.asm --target vec
  "$SJ" --lst=build/knightlore_vec.lst --sld=build/knightlore_vec.sld --fullpath build/knightlore_vec.asm
  python3 tools/check_i8080.py build/knightlore_vec.lst
  python3 tools/check_ixflags.py build/knightlore_vec.asm
  python3 tools/check_plane2.py build/knightlore_vec.lst
  python3 tools/mkrelease.py build/knightlore_vec.r0m build/knightlore_title.asm
  "$SJ" --lst=build/knightlore_title.lst --fullpath build/knightlore_title.asm
  python3 tools/check_i8080.py build/knightlore_title.lst
  ls -la build/knightlore_title.r0m
elif [ "$TARGET" = "z80vec" ]; then
  python3 tools/kl_z80_to_8080.py src/knightlore_z80.asm build/knightlore_z80.asm --target z80vec
  "$SJ" --lst=build/knightlore_z80.lst --sld=build/knightlore_z80.sld --fullpath build/knightlore_z80.asm
  ls -la build/knightlore_z80.r0m
else
  python3 tools/kl_z80_to_8080.py src/knightlore_z80.asm build/knightlore_8080.asm --target $TARGET
  "$SJ" --lst=build/knightlore_8080.lst --sld=build/knightlore_8080.sld --fullpath build/knightlore_8080.asm
  python3 tools/check_i8080.py build/knightlore_8080.lst
  python3 tools/check_ixflags.py build/knightlore_8080.asm
  ls -la build/knightlore_8080.r0m
fi
