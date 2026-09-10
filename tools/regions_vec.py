#!/usr/bin/env python3
"""Границы регионов раскладки по листингу: конец нижнего региона кода и конец верхнего.
  python3 tools/regions_vec.py build/knightlore_vec.lst
Переменные (clear_start, 0x7A80+) и буфер не считаются: берутся только строки с выданными байтами
ниже потолка кода и в верхнем регионе."""
import sys, re
lst = sys.argv[1] if len(sys.argv) > 1 else 'build/knightlore_vec.lst'
RE_B = re.compile(r'^\s*\d+\+?\s+([0-9A-Fa-f]{4})\s((?:\s?[0-9A-Fa-f]{2}){1,4})\s')
TOP = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0x7A00
lomax = 0; upmin = 0x10000; upmax = 0; plane2 = []
for line in open(lst, encoding='latin-1'):
    m = RE_B.match(line)
    if not m:
        continue
    a = int(m.group(1), 16); n = len(m.group(2).split())
    if a < TOP:
        lomax = max(lomax, a + n)
    elif 0xC000 <= a < 0xF100:
        upmin = min(upmin, a); upmax = max(upmax, a + n)
    if 0xC800 <= a < 0xE000:
        plane2.append((a, n, line.rstrip()[:110]))
print("низ:  конец %04X, свободно до %04X: %d" % (lomax, TOP, TOP - lomax))
print("верх: %04X..%04X = %d байт" % (upmin, upmax, upmax - upmin))
print("в плоскости 2 (0xC800..0xDFFF): %d байт" % sum(n for _, n, _ in plane2))
for a, n, t in plane2[:5]:
    print("   ", t)
