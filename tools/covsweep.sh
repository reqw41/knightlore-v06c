#!/bin/bash
# ОБЪЕДИНЁННОЕ ПОКРЫТИЕ (§31.3): много сценариев в одну карту build/coverage_union.bin.
#   tools/covsweep.sh [итераций-в-комнате=60] [итераций-ходьбы=1500]
# Сценарии:
#   1) ВСЕ 256 номеров комнаты (валидных 128) от меню до N итераций покоя — coverage_vec.js
#      ведёт карту с самого сброса, поэтому сюда попадают загрузчик, меню, вход в комнату,
#      обработчики объектов каждой комнаты;
#   2) ходьба по зерну со сменой комнат (KLARCH), сменой дня/ночи (KLDAY) и бессмертием —
#      logictrace_vec.js, три зерна: подбор/бросок, арки, превращение в оборотня;
#   3) то же БЕЗ бессмертия: смерть, потеря жизни, GAME OVER и возврат в меню.
# Карта — побитовое ИЛИ по всем прогонам (KLCOVU), 1 байт на адрес.
set -e
cd "$(dirname "$0")/.."
N=${1:-60}; W=${2:-1500}
U=build/coverage_union.bin
rm -f "$U"
ROMS="build/knightlore_vec.r0m build/knightlore_vec.sld"
rooms=$(python3 -c "print(','.join(str(i) for i in range(256)))")
echo "--- 1. все номера комнат ($N итераций в каждой)"
KLCOVU=$U KLCOVQ=1 KL_SND=vi node tools/coverage_vec.js $ROMS "$N" "start,$rooms" | tail -2
echo "--- 2. ходьба со сменой комнат и дня/ночи ($W итераций)"
for s in 3 7 11; do
  KLCOVU=$U KLSEED=$s KLARCH=11 KLDAY=61 KLLIVES=1 KL_SND=vi \
    node tools/logictrace_vec.js $ROMS "$W" build/cov_lt$s.txt | tail -1
done
echo "--- 3. смерть и GAME OVER (без бессмертия)"
for s in 5 9; do
  KLCOVU=$U KLSEED=$s KLARCH=7 KL_SND=vi \
    node tools/logictrace_vec.js $ROMS "$W" build/cov_dd$s.txt | tail -1
done
python3 - "$U" <<'EOF'
import sys
d=open(sys.argv[1],'rb').read()
print("покрыто байт всего:", sum(1 for b in d if b))
EOF
