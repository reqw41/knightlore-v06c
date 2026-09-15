#!/bin/bash
# Гейт звука: все ПЯТЬ мелодий против эталонной модели MSX (§28).
#   tools/aygate.sh [кадр нажатия]        по умолчанию 200
# Собирает цель jukebox, по одной мелодии на прогон снимает журнал AY и сверяет aycheck'ом.
# rc = 0 только если все пять дали ПОЛНОЕ СОВПАДЕНИЕ.
#
# Клавишу нажимаем в кадре >= 90 (цикл меню заводится к кадру ~87): раньше нажатие пропадает
# молча, журнал остаётся журналом мотива меню и сверка считает расхождения с чужой мелодией.
# Сам aytrace это теперь ловит (rc=1), а aycheck опознаёт мелодию по длине куска журнала.
#
# game_complete: её потоки начинаются сразу с ноты, режим громкости остаётся от ПРЕДЫДУЩЕЙ
# мелодии (дефект оригинала, §23.5). Здесь перед ней всегда играет мотив меню -> --mode=0,1,2.
set -u
cd "$(dirname "$0")/.."
AT=${1:-200}
UP=$((AT + 4))
OUT=$(mktemp -d)
trap 'rm -rf "$OUT"' EXIT

blog=$(./build.sh jukebox 2>&1) || { echo "aygate: сборка jukebox упала"; echo "$blog"; exit 1; }
ROM=build/knightlore_jukebox.r0m

# клавиша, мелодия, кадров прогона (мелодия + запас), доп. ключи aycheck
SET="d menu 1600
e start_game 500
f game_over 700
g game_complete 700 --mode=0,1,2
h blip 400"

bad=0
while read -r key tune frames extra; do
  [ -n "$key" ] || continue
  log="$OUT/$tune.txt"
  if ! node tools/aytrace_vec.js "$ROM" "$frames" --snd=vi \
        "--keys=$AT:$key:down,$UP:$key:up" "--out=$log" >/dev/null; then
    echo "aygate: $tune — СТЕНД: нажатие не дошло до игры"; bad=$((bad + 1)); continue
  fi
  res=$(python3 tools/aycheck.py "$log" "$tune" $extra 2>&1)
  if [ $? -ne 0 ]; then
    echo "aygate: $tune — КРАСНЫЙ"; echo "$res" | sed 's/^/    /'; bad=$((bad + 1))
  else
    echo "aygate: $tune — $(echo "$res" | tail -1) ($(echo "$res" | grep -o 'модель [0-9]*' | head -1) записей)"
  fi
done <<< "$SET"

[ "$bad" = 0 ] && echo "aygate: все пять мелодий совпали с моделью MSX" || echo "aygate: красных мелодий $bad"
exit $([ "$bad" = 0 ] && echo 0 || echo 1)
