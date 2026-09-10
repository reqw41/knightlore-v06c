#!/usr/bin/env python3
"""Гейт «плоскость 2 чиста»: в 0xC000..0xDFFF не должно быть НИЧЕГО в строках картинки (32..223).

Биты плоскости 2 — селектор белого цвета HUD (vec_set_pal/vec_hud_mask), поэтому любой байт кода
или данных в строках 32..223 её столбцов белил бы точки поля. Строки 0..31 и 224..255 вне картинки
и отданы под код (vec_pack_holes) — они разрешены.

  python3 tools/check_plane2.py build/knightlore_vec.lst
"""
import sys, re

lst = sys.argv[1]
RE_B = re.compile(r'^\s*\d+\+?\s+([0-9A-Fa-f]{4})\s((?:\s?[0-9A-Fa-f]{2}){1,4})\s')
bad = []
holes = 0
for line in open(lst, encoding='latin-1'):
    m = RE_B.match(line)
    if not m:
        continue
    a = int(m.group(1), 16)
    for k in range(len(m.group(2).split())):
        b = a + k
        if not 0xC000 <= b < 0xE000:
            continue
        if 32 <= (b & 0xFF) <= 223:
            bad.append((b, line.rstrip()[:100]))
        else:
            holes += 1
if bad:
    print("check_plane2: В КАРТИНКЕ ПЛОСКОСТИ 2 %d байт (столбец %d, строка %d и далее):"
          % (len(bad), (bad[0][0] >> 8) & 0x1F, bad[0][0] & 0xFF))
    for b, t in bad[:5]:
        print("   %04X  %s" % (b, t))
    sys.exit(1)
print("check_plane2: строки 32..223 плоскости 2 чисты; в дырах вне картинки %d байт" % holes)
