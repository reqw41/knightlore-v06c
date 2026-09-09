#!/bin/bash
# Сборка Knight Lore для Вектора: Z80-исходник -> транслятор -> sjasmplus -> build/knightlore_8080.r0m + гейт i8080
set -e
cd "$(dirname "$0")"
TARGET=${1:-lockstep}
mkdir -p build
python3 tools/kl_z80_to_8080.py src/knightlore_z80.asm build/knightlore_8080.asm --target $TARGET
./tools/sjasmplus --lst=build/knightlore_8080.lst --sld=build/knightlore_8080.sld --fullpath build/knightlore_8080.asm
python3 tools/check_i8080.py build/knightlore_8080.lst
ls -la build/knightlore_8080.r0m
