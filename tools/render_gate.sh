#!/bin/bash
# Гейт нативного рендера: сравнить ТЕКУЩЕЕ дерево с эталоном (ревизия, по умолчанию HEAD).
#   tools/render_gate.sh [ревизия-эталона] [итераций]
# 1) разворачивает ревизию в build/ref, собирает её -> профиль покоя + снимки экрана/буфера;
# 2) собирает текущее дерево -> то же самое;
# 3) сверяет снимки побайтно (покой и inputs_alive) и печатает дельту тактов и границы регионов.
# Ввод в screendiff_vec.js кладётся прямо в vec_zxrows на границе итерации, поэтому сравнение
# по сценарию ходьбы законно (иначе разная скорость сборок = разные кадры опроса клавиш).
set -e
cd "$(dirname "$0")/.."
ROOT=$(pwd)
REF=${1:-HEAD}; N=${2:-300}
ALIVE=$(cd ../../knightlore-research/zx-stand && pwd)/inputs_alive.txt
run() { # $1 = метка, cwd = дерево сборки
  ./build.sh vec >/dev/null
  node "$ROOT"/tools/screendiff_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld "$N" "" "$1"_rest >/dev/null
  node "$ROOT"/tools/screendiff_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld "$N" "$ALIVE" "$1"_alive >/dev/null
  node "$ROOT"/tools/profile_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld "$N" | head -1
  echo -n "   границы регионов (ASSERT 0x7E00 / 0xF100): "
  grep -E 'ASSERT \$ <= 0x(7E00|F100)' build/knightlore_vec.lst | awk '{print $2}' | tr '\n' ' '
  echo
}
rm -rf build/ref; mkdir -p build/ref
git archive "$REF" | tar -x -C build/ref
( cd build/ref && run ref > "$ROOT"/build/prof_ref.txt )
run new > build/prof_new.txt
rc=0
for t in rest alive; do
  if cmp -s build/ref/build/scr_ref_"$t".bin build/scr_new_"$t".bin && cmp -s build/ref/build/scr_ref_"$t".txt build/scr_new_"$t".txt
  then echo "картинка $t: СОВПАДЕНИЕ ($N итераций, буфер+экран байт в байт)"
  else echo "картинка $t: РАСХОЖДЕНИЕ — первая итерация:"; diff build/ref/build/scr_ref_"$t".txt build/scr_new_"$t".txt | head -3; rc=1; fi
done
echo "эталон ($REF):"; cat build/prof_ref.txt
echo "текущее:";       cat build/prof_new.txt
exit $rc
