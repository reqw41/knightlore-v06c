#!/usr/bin/env python3
"""Инвентаризация памяти по листингу: сколько байт занимает каждая процедура/блок данных.

  python3 tools/sizemap_vec.py build/knightlore_vec.lst [минимум байт] [--all]

Байты берутся из .lst (адрес + выданные байты), приписываются ближайшей ВЕРХНЕЙ метке
верхнего уровня (внутренние loc_/./il_/ps_/... сливаются с охватывающей). Печатает таблицу
по убыванию размера, сводку по категориям (спрайты / прочее) и по регионам раскладки Вектора.
"""
import sys, re
from collections import defaultdict

lst = sys.argv[1]
minb = 0
show_all = '--all' in sys.argv
for a in sys.argv[2:]:
    if a.isdigit():
        minb = int(a)

REGIONS = [(0x0000, 0x00A0, 'boot'), (0x00A0, 0x7E00, 'низ'), (0x7E00, 0x8000, 'стек'),
           (0x8000, 0xA000, 'экран'), (0xA000, 0xC000, 'буфер'), (0xC000, 0xC800, 'защита'),
           (0xC800, 0xCF00, 'перем'), (0xCF00, 0xD400, 'zx_aram'), (0xD400, 0xF100, 'верх'),
           (0xF100, 0x10000, 'таблицы')]

def region(a):
    for lo, hi, nm in REGIONS:
        if lo <= a < hi:
            return nm
    return '?'

# строки листинга sjasmplus: "<номер> <AAAA> <HH HH ...>  <текст>" или "<номер> <AAAA>   <текст>"
RE_B = re.compile(r'^\s*\d+\+?\s+([0-9A-Fa-f]{4})\s((?:\s?[0-9A-Fa-f]{2}){1,4})\s*(.*)$')
RE_N = re.compile(r'^\s*\d+\+?\s+([0-9A-Fa-f]{4})\s+(.*)$')
RE_LBL = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*):')
INNER = re.compile(r'^(loc_|IL_|il_|ps_|rp_|vb_|us_|bts_|vfw_|vdb_|vpb_|usl_|kl_b6|kl_frv|vec_zx|\.)')

size = defaultdict(int)
addr = {}
cur = '(до первой метки)'
for line in open(lst, encoding='latin-1'):
    txt = line.rstrip('\n')
    m = RE_B.match(txt)
    if m:
        a = int(m.group(1), 16); nb = len(m.group(2).split()); body = m.group(3)
    else:
        m2 = RE_N.match(txt)
        if not m2:
            continue
        a = int(m2.group(1), 16); nb = 0; body = m2.group(2)
    lm = RE_LBL.match(body.strip())
    if lm:
        nm = lm.group(1)
        if not INNER.match(nm):
            cur = nm
            if nm not in addr:
                addr[nm] = a
    if nb:
        if cur not in addr or addr[cur] > a:
            addr[cur] = a
        size[cur] += nb

rows = sorted(size.items(), key=lambda kv: -kv[1])
print('метка                                адрес  байт  регион')
for n, sz in rows:
    if sz >= minb and (show_all or not n.startswith('spr_')):
        print(f'{n:<36} {addr.get(n,0):04X} {sz:5}  {region(addr.get(n,0))}')

cat = defaultdict(int)
reg = defaultdict(int)
for n, sz in size.items():
    a = addr.get(n, 0)
    reg[region(a)] += sz
    cat['спрайты' if n.startswith('spr_') else 'прочее'] += sz
print('\nпо категориям:', ', '.join(f'{k}={v}' for k, v in sorted(cat.items())))
print('по регионам:  ', ', '.join(f'{k}={v}' for k, v in sorted(reg.items())))
print('всего байт:', sum(size.values()))
