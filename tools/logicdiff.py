#!/usr/bin/env python3
"""logicdiff.py <A.bin> <A.map> <B.bin> <B.map> — сверка журналов logictrace_vec.js двух целей.

Печатает по итерациям, какие ИМЕНОВАННЫЕ переменные разошлись, и сводку «сколько итераций
каждую переменную видели разной». Так видно, расходится ли сама игровая логика или только
служебные ячейки, которых у одной цели нет (звук MSX, клавиатура Вектора и т. п.).
Код возврата 1, если после исключений остались расхождения.
"""
import sys, collections


def load(mapf):
    names, rec = [], None
    for l in open(mapf):
        q = l.split()
        if q[0] == 'REC':
            rec = int(q[1]); continue
        names.append((int(q[0]), q[1]))
    names.sort()
    return names, rec


def name_of(names, off):
    best = '?'
    for a, n in names:
        if a <= off:
            best = n + ('+%d' % (off - a) if off != a else '')
        else:
            break
    return best


A, AM, B, BM = sys.argv[1:5]
SKIP = set(sys.argv[5].split(',')) if len(sys.argv) > 5 else set()
na, ra = load(AM)
nb, rb = load(BM)
assert ra == rb, 'разный размер блока переменных: %d против %d' % (ra, rb)
da, db = open(A, 'rb').read(), open(B, 'rb').read()
n = min(len(da), len(db)) // ra
hits = collections.Counter()
first = {}
for it in range(n):
    sa, sb = da[it * ra:(it + 1) * ra], db[it * ra:(it + 1) * ra]
    if sa == sb:
        continue
    for off in range(ra):
        if sa[off] != sb[off]:
            nm = name_of(na, off)
            base = nm.split('+')[0]
            if base in SKIP:
                continue
            hits[nm] += 1
            first.setdefault(nm, (it + 1, sa[off], sb[off]))
print('итераций сверено %d, записей %d байт' % (n, ra))
if not hits:
    print('СОВПАДЕНИЕ: игровое состояние двух целей одинаково')
    sys.exit(0)
print('РАСХОЖДЕНИЯ (переменная: итераций разной, первая итерация, A -> B):')
for nm, c in hits.most_common(60):
    it, x, y = first[nm]
    print('  %-34s %6d   первая итер %5d  %02X -> %02X' % (nm, c, it, x, y))
print('всего разных ячеек: %d' % len(hits))
sys.exit(1)
