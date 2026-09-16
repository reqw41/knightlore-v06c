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
# ВТОРОЕ ПЕРО (§32): состояния, до которых 30 снимков выше не доходят — смена дня (счётчик
# перепечатывает print_days + blit_2x8), ночь (в арке луна, а не солнце) и оба сразу.
# Смена дня без тысячи итераций: знак идёт по арке на точку за 8 итераций и меняет день,
# дойдя до X=225; кладём X=224 дважды (первый переход — на луну, день растёт на втором).
DAYPOKE="sun_moon_scratchpad+26=224@5;sun_moon_scratchpad+26=224@30"
for it in 60 1500 2000; do
  n=$((n+1))
  KLGAG=1 KLLIVES=1 KLPOKE="$POKE${POKE:+;}$([ $it = 60 ] && echo "$DAYPOKE")" KLDUMP=build/hs_dump.txt \
    node tools/hudshot_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld "$it" build/hs.png >/dev/null || exit 2
  if ! out=$(python3 tools/hudcolor.py build/hs_dump.txt); then
    bad=$((bad+1)); echo "!!! смена дня/ночь, итераций $it:"; echo "$out" | grep '^!!!'
  fi
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
  # KLITEMS=1 — три предмета в инвентаре: без них ЖЁЛТАЯ зона предметов пуста и инвариант
  # второго пера (§32) по ней ничего не проверяет
  if ! env KLLIVES=1 KLITEMS=1 KLDIRS=$seq KLCSV=build/hudguard_exit_$tag.csv \
       node tools/hudguard_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld \
            "${KLEXITN:-120}" start > build/hudguard_exit_$tag.log 2>&1; then ex=$((ex+1)); fi
  grep -E "выходы через арки|ГЕЙТ" build/hudguard_exit_$tag.log
done
# Сетка KLFORCE ставит героя в углы комнаты и на 0x30..0x60 НИЖЕ пола — это проверка КОДА
# рендера (§29.7), а не достижимое положение: естественная ходьба даёт Z = 0x80 ровно. Поэтому
# здесь KLNOPEN=1: инвариант второго пера (§32) в этом сценарии неприменим, стирание объекта
# законно трёт буфер под ячейками предметов. Проверку пера по всем комнатам даёт прогон БЕЗ
# принудительной сетки (см. §32: 128 комнат x 5 направлений, 25 600 границ итераций).
if ! env KLLIVES=1 KLITEMS=1 KLNOPEN=1 KLFORCE=1 KLDIRS=n KLCSV=build/hudguard_force.csv \
     node tools/hudguard_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld \
          "${KLFORCEN:-40}" "${KLFORCEROOMS:-all}" > build/hudguard_force.log 2>&1; then ex=$((ex+1)); fi
tail -2 build/hudguard_force.log
echo "выход через арки + сетка: красных прогонов $ex"
exit $((bad+ex))
