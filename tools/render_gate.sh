#!/bin/bash
# Гейт нативного рендера: сравнить ТЕКУЩЕЕ дерево с эталоном (ревизия, по умолчанию HEAD).
#   tools/render_gate.sh [ревизия-эталона] [итераций] [комнаты через запятую]
# 1) разворачивает ревизию в build/ref, собирает её -> профиль покоя + снимки экрана/буфера;
# 2) собирает текущее дерево -> то же самое;
# 3) сверяет снимки побайтно (покой, inputs_alive и каждая комната из списка) и печатает дельту
#    тактов и границы регионов.
# Ввод в screendiff_vec.js кладётся прямо в vec_zxrows на границе итерации, поэтому сравнение
# по сценарию ходьбы законно (иначе разная скорость сборок = разные кадры опроса клавиш).
# Комнаты подменяются через KLROOM (как в profile_vec.js), сценарий в комнате — покой.
set -e
cd "$(dirname "$0")/.."
ROOT=$(pwd)
REF=${1:-HEAD}; N=${2:-300}; ROOMS=${3:-39,163,3,133}
ALIVE=$(cd ../../knightlore-research/zx-stand && pwd)/inputs_alive.txt
IFS=, read -r -a ROOMA <<< "$ROOMS"
SCEN=(rest alive); for r in "${ROOMA[@]}"; do SCEN+=("r$r"); done
run() { # $1 = метка, cwd = дерево сборки
  ./build.sh vec >/dev/null
  node "$ROOT"/tools/screendiff_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld "$N" "" "$1"_rest >/dev/null
  node "$ROOT"/tools/screendiff_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld "$N" "$ALIVE" "$1"_alive >/dev/null
  for r in "${ROOMA[@]}"; do
    KLROOM=$r node "$ROOT"/tools/screendiff_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld "$N" "" "$1"_r"$r" >/dev/null
  done
  node "$ROOT"/tools/profile_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld "$N" | head -1
  for r in "${ROOMA[@]}"; do
    echo -n "   комната $r: "; KLROOM=$r node "$ROOT"/tools/profile_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld "$N" | head -1
  done
  echo -n "   границы регионов (конец нижнего / конец верхнего): "
  grep -E 'ASSERT \$ <=.*(нижний регион|верхний регион)' build/knightlore_vec.lst | awk '{print $2}' | tr '\n' ' '
  echo
}
rm -rf build/ref; mkdir -p build/ref
git archive "$REF" | tar -x -C build/ref
# инструменты берём ВСЕГДА из текущего дерева (KLROOM, метки сценариев): эталон — только код игры
( cd build/ref && run ref > "$ROOT"/build/prof_ref.txt )
run new > build/prof_new.txt
rc=0
for t in "${SCEN[@]}"; do
  if cmp -s build/ref/build/scr_ref_"$t".bin build/scr_new_"$t".bin && cmp -s build/ref/build/scr_ref_"$t".txt build/scr_new_"$t".txt
  then echo "картинка $t: СОВПАДЕНИЕ ($N итераций, буфер+экран байт в байт)"
  else echo "картинка $t: РАСХОЖДЕНИЕ — первая итерация:"; diff build/ref/build/scr_ref_"$t".txt build/scr_new_"$t".txt | head -3; rc=1; fi
done
echo "эталон ($REF):"; cat build/prof_ref.txt
echo "текущее:";       cat build/prof_new.txt
exit $rc
