#!/bin/bash
# ГЕЙТ ЛОГИКИ ЦЕЛИ vec ПРОТИВ ЦЕЛИ lockstep на сценарии СО СМЕНОЙ КОМНАТ.
# Локстеп против ZX (tools/lockstep_vec.js) гоняет 600 итераций в стартовой комнате и перехода
# между комнатами НЕ КАСАЕТСЯ вовсе; а именно там живёт склейка exit_screen (`inc sp` x4),
# перестройка комнаты и палитра. Здесь эталоном служит сама цель lockstep (её логика 1:1 с ZX
# и проверена локстепом), а сверяется с ней цель vec — с нативным рендером, звуком MSX и своей
# клавиатурой. Обе цели гоняются ОДНИМ сценарием: ходьба по зерну, принудительный переход между
# комнатами (взвод «рядом с аркой» + край комнаты по очереди на 4 стороны), смена дня/ночи.
#   tools/logicgate.sh [итераций] [зёрна через запятую]
# Законные расхождения (исключены по именам): 5 указателей локстепа, tmp_attrib (атрибутов ZX
# на Векторе нет), user_input бит 5 (directional pickup; в режиме 0 не читается — chk_pickup_drop
# уходит на loc_C00B и берёт только 0x10), objs_wiped_cnt (у vec копию делает нативный рендер,
# а render_dynamic_objects обнуляет счётчик в начале КАЖДОЙ итерации, и rendered_objs_cnt совпадает).
set -e
cd "$(dirname "$0")/.."
N=${1:-1500}; SEEDS=${2:-3,7}
SKIP=tmp_SP,render_obj_1,render_obj_2,tmp_objects_to_draw,gfxbase_8x8,tmp_attrib,user_input,objs_wiped_cnt
./build.sh lockstep >/dev/null
./build.sh vec >/dev/null
rc=0
IFS=, read -r -a SA <<< "$SEEDS"
for s in "${SA[@]}"; do
  env KLSEED=$s KLARCH=11 KLDAY=61 KLLIVES=1 node tools/logictrace_vec.js build/knightlore_8080.r0m build/knightlore_8080.sld "$N" build/lg_ls$s.txt | tail -1
  env KLSEED=$s KLARCH=11 KLDAY=61 KLLIVES=1 node tools/logictrace_vec.js build/knightlore_vec.r0m  build/knightlore_vec.sld  "$N" build/lg_vc$s.txt | tail -1
  echo "--- зерно $s ---"
  python3 tools/logicdiff.py build/lg_ls$s.bin build/lg_ls$s.map build/lg_vc$s.bin build/lg_vc$s.map "$SKIP" || rc=1
  d=$(diff <(cut -d' ' -f1,2 build/lg_ls$s.txt) <(cut -d' ' -f1,2 build/lg_vc$s.txt) | wc -l)
  if [ "$d" -eq 0 ]; then echo "последовательность комнат совпала"; else echo "ПОСЛЕДОВАТЕЛЬНОСТЬ КОМНАТ РАЗОШЛАСЬ"; rc=1; fi
done
exit $rc
