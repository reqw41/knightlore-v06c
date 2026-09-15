#!/bin/sh
# hudsweep.sh [итераций-список] — 30 снимков HUD (6 комнат x 5 положений знака солнца/луны)
# и прогон tools/hudcolor.py по каждому: «каждая связная компонента картинки HUD одного цвета».
# KLITEMS=1 — принудительно положить три предмета в инвентарь (снимок белых ячеек).
# Код возврата: число снимков, на которых hudcolor.py покраснел.
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
exit $bad
