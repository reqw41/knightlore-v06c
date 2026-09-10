#!/bin/bash
# Гейт нативного рендера: сравнить ТЕКУЩЕЕ дерево с эталоном (ревизия, по умолчанию HEAD).
#   tools/render_gate.sh [ревизия-эталона] [итераций]
# 1) собирает эталон (tools/kl_native_render.py из ревизии) -> профиль покоя + снимки экрана/буфера;
# 2) собирает текущее дерево -> то же самое;
# 3) сверяет снимки побайтно (покой и inputs_alive) и печатает дельту тактов.
# Ввод в screendiff_vec.js кладётся прямо в vec_zxrows на границе итерации, поэтому сравнение
# по сценарию ходьбы законно (иначе разная скорость сборок = разные кадры опроса клавиш).
set -e
cd "$(dirname "$0")/.."
REF=${1:-HEAD}; N=${2:-300}
ALIVE=../../knightlore-research/zx-stand/inputs_alive.txt
run() { # $1 = метка
  ./build.sh vec >/dev/null
  node tools/screendiff_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld "$N" "" "$1"_rest >/dev/null
  node tools/screendiff_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld "$N" "$ALIVE" "$1"_alive >/dev/null
  node tools/profile_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld "$N" | head -1 > build/prof_"$1".txt
  grep -E 'ASSERT \$ <= 0x(7E00|F100)' build/knightlore_vec.lst | awk '{print $2}' | tr '\n' ' ' >> build/prof_"$1".txt
  echo >> build/prof_"$1".txt
}
cp tools/kl_native_render.py build/kl_native_render.cur
git show "$REF":tools/kl_native_render.py > tools/kl_native_render.py
run ref
cp build/kl_native_render.cur tools/kl_native_render.py
run new
rc=0
for t in rest alive; do
  if cmp -s build/scr_ref_"$t".bin build/scr_new_"$t".bin && cmp -s build/scr_ref_"$t".txt build/scr_new_"$t".txt
  then echo "картинка $t: СОВПАДЕНИЕ ($N итераций, буфер+экран байт в байт)"
  else echo "картинка $t: РАСХОЖДЕНИЕ — первая итерация:"; diff build/scr_ref_"$t".txt build/scr_new_"$t".txt | head -3; rc=1; fi
done
echo "эталон ($REF): $(cat build/prof_ref.txt | tr '\n' ' ')"
echo "текущее:       $(cat build/prof_new.txt | tr '\n' ' ')"
exit $rc
