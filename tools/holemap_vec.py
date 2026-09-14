#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Карта запасов раскладки vec по листингу: конец нижнего региона, хвост верхнего, остатки в дырах.
   python3 tools/holemap_vec.py build/knightlore_vec.lst"""
import re, sys

lst = sys.argv[1] if len(sys.argv) > 1 else 'build/knightlore_vec.lst'
rows = []
for l in open(lst, encoding='utf-8', errors='replace'):
    m = re.match(r'^\s*\d+\+?\s+([0-9A-F]{4})\s(.*)$', l.rstrip('\n'))
    if m:
        rows.append((int(m.group(1), 16), (m.group(2)[13:] if len(m.group(2)) > 13 else '').strip()))
al = [a for a, s in rows if s.startswith('ALIGN 256')]
up = [a for a, s in rows if 'ASSERT $ <= 0xF100' in s]
print("низ:  код кончается %04X, свободно до vec_rptab (0x7900): %d Б" % (al[-1], 0x7900 - al[-1]))
print("верх: кончается %04X, свободно до таблиц сдвига (0xF100): %d Б" % (up[0], 0xF100 - up[0]))
holes = []
for a, s in rows:
    m = re.search(r'ASSERT \$ <= (0x[0-9a-f]+)\s*;\s*\S+ (0x[0-9a-f]+) \((\d+)', s)
    if m:
        holes.append((int(m.group(2), 16), int(m.group(3)), a))

def vh(base, pages=32):
    h = [(base, 32)]
    h += [(base + c * 256 + 224, 64) for c in range(pages - 1)]
    h.append((base + (pages - 1) * 256 + 224, 32))
    return h

ALL = [h for b in (0xA000, 0xC000) for h in vh(b)] + [(0x0007, 0x29), (0x003B, 5), (0x00D1, 0x2F), (0x7FE8, 0x18)]
used = {b: e for b, sz, e in holes}
free = sum((b + sz - used[b]) if b in used else sz for b, sz in ALL)
print("дыры: занято %d из %d, свободно %d Б из %d" % (len(holes), len(ALL), free, sum(s for _, s in ALL)))
