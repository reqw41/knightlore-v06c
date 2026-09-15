#!/bin/bash
# hudsweep.sh [итераций-список] — 30 снимков HUD (6 комнат x 5 положений знака солнца/луны)
# и прогон tools/hudcolor.py по каждому: «каждая связная компонента картинки HUD одного цвета».
# Фаза 2 (§30): tools/hudguard_vec.js на сценарии РЕАЛЬНОГО выхода через арки + принудительная сетка.
# KLITEMS=1 — принудительно положить три предмета в инвентарь (снимок белых ячеек).
# Код возврата: число снимков, на которых hudcolor.py покраснел, ПЛЮС число красных прогонов
# гейта панели (фаза 2, §30). KLNOEXIT=1 — только фаза 1; KLEXITN/KLFORCEN — итерации фаз.
set -u
cd "$(dirname "$0")/.."
ROOMS="${KLROOMS:-start 39 163 133 31 3}"
ITERS="${1:-80 110 140 170 200}"
POKE=""
[ "${KLITEMS:-}" = "1" ] && POKE="objects_carried=96@5;objects_carried+4=97@5;objects_carried+8=98@5;objects_carried_changed=1@6"
bad=0; n=0
for r in $ROOMS; do
  for it in $ITERS; do
    n=$((n+1))
    if [ "$r" = start ]; then unset KLROOM 2>/dev/null || true; else KLROOM=$r; export KLROOM; fi
    KLGAG=1 KLLIVES=1 KLPOKE="$POKE" KLDUMP=build/hs_dump.txt \
      node tools/hudshot_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld "$it" build/hs.png >/dev/null || exit 2
    if ! out=$(python3 tools/hudcolor.py build/hs_dump.txt); then
      bad=$((bad+1)); echo "!!! комната $r, итераций $it:"; echo "$out" | grep '^!!!'
    fi
  done
done
echo "снимков $n, разноцветных $bad"

# ---- ФАЗА 2 (§30): «панель не затирают» на СЦЕНАРИИ РЕАЛЬНОГО ВЫХОДА ЧЕРЕЗ АРКИ ----
# Прежний прогон §29 гонял 128 ПОДМЕНЁННЫХ комнат — и ни разу не довёл окно объекта до кромки
# комнаты: при подмене номера игра ставит героя в середину ЧУЖОЙ комнаты, где он упирается в
# препятствие и только перебирает ногами (X/Y не меняются ни на шаг). Поэтому здесь комната
# «start» — та, куда игра ставит героя сама, — и герой РЕАЛЬНО выходит через арки: на юг (нижняя
# левая арка — тот самый дефект), на запад, на север и цепочкой 47->63->79->95->111.
# Восток из стартовой комнаты недостижим (герой упирается в стену), правую половину лестницы
# закрывает прогон с принудительной сеткой положений (KLFORCE): он достаёт до столбцов 23..29.
[ "${KLNOEXIT:-}" = "1" ] && exit $bad
ex=0
for seq in down left up up,up,up; do
  tag=${seq//,/_}
  if ! env KLLIVES=1 KLDIRS=$seq KLCSV=build/hudguard_exit_$tag.csv \
       node tools/hudguard_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld \
            "${KLEXITN:-120}" start > build/hudguard_exit_$tag.log 2>&1; then ex=$((ex+1)); fi
  grep -E "выходы через арки|ГЕЙТ" build/hudguard_exit_$tag.log
done
if ! env KLLIVES=1 KLFORCE=1 KLDIRS=n KLCSV=build/hudguard_force.csv \
     node tools/hudguard_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld \
          "${KLFORCEN:-40}" "${KLFORCEROOMS:-all}" > build/hudguard_force.log 2>&1; then ex=$((ex+1)); fi
tail -2 build/hudguard_force.log
echo "выход через арки + сетка: красных прогонов $ex"
exit $((bad+ex))
