#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""АУДИТ МЁРТВОГО КОДА (§31.3): динамика (покрытие) + статика (ссылки на метку).

Блок кода мёртв ТОЛЬКО если он и холодный, и недостижимый статически: на его метку нет
ни одной ссылки, кроме собственного объявления, ИЛИ все ссылающиеся блоки сами мертвы.
Одного покрытия мало (сценарий мог не дойти), одной статики мало (адрес может лежать
в таблице переходов или в данных).

  python3 tools/deadcode_vec.py build/knightlore_vec.lst [build/coverage_union.bin] [--all]

Ссылками считаются ЛЮБЫЕ упоминания имени метки в исходном тексте листинга (операнды
call/jp/jr/ld/dw и т. п.), кроме строки самого объявления; комментарии отрезаются.
Так таблица переходов (upd_sprite_jmp_tbl, special_objs_tbl, ret_from_tbl_jp) и адреса
в данных считаются ссылками автоматически. ПРОВАЛ (fall-through) с предыдущего блока
тоже считается ссылкой: блок, в который можно провалиться, живой, если жив предыдущий.
"""
import re
import sys

ARGS = [a for a in sys.argv[1:] if not a.startswith('--')]
ALL = '--all' in sys.argv
LST = ARGS[0] if ARGS else 'build/knightlore_vec.lst'
COV = ARGS[1] if len(ARGS) > 1 else None

# метки, которые НЕ начинают отдельный блок (внутренние метки процедур и склейки)
INNER = re.compile(r'^(loc_|IL_|ps_|rp_|vb_|us_|bts_|vfw_|vdb_|vpb_|usl_|vp_|vi_|vhm_|kl_b6|kl_frv|zb_|\.)')
# инструкции, после которых управление НЕ переходит на следующий байт
NOFALL = re.compile(r'^\s*(ret|jp|jr|halt)\b(?!\s+(n?z|n?c|p[oe]?|m)\b)', re.I)

rows = []           # (addr, nbytes, src)
for line in open(LST, encoding='latin1'):
    m = re.match(r'^\s*\d+\+?\s+([0-9A-F]{4})\s((?:\s?[0-9A-F]{2}){1,4})\s*(.*)$', line.rstrip('\n'))
    if m:
        rows.append((int(m.group(1), 16), len(m.group(2).split()), m.group(3)))
        continue
    m = re.match(r'^\s*\d+\+?\s+([0-9A-F]{4})\s+(.*)$', line.rstrip('\n'))
    if m:
        rows.append((int(m.group(1), 16), 0, m.group(2)))

blocks = []         # [имя, адрес, байт, провал_из_предыдущего, исходник блока]
cur = None
prev_fall = False
for addr, nb, src in rows:
    body = src.split(';')[0].rstrip()
    lm = re.match(r'^([A-Za-z_][\w]*):', body.strip())
    if lm and not INNER.match(lm[1]):
        blocks.append([lm[1], addr, 0, prev_fall, [], False])
        cur = blocks[-1]
        prev_fall = True
    if cur is not None:
        cur[2] += nb
        if body.strip():
            cur[4].append(body)
        if nb:
            rest = re.sub(r'^[A-Za-z_][\w]*:', '', body).strip()
            if rest:
                # данные (DB/DW/DS/DC) цепочку провала обрывают: в таблицу не «проваливаются»,
                # в неё приходят по ссылке, и живой она считается только по ссылке
                prev_fall = False if re.match(r'^(db|dw|ds|dc|dz|dh)\b', rest, re.I) else not NOFALL.match(' ' + rest)
                cur[5] = cur[5] or bool(re.match(r'^(db|dw|ds|dc|dz|dh)\b', rest, re.I))

names = {b[0]: i for i, b in enumerate(blocks)}
# ссылки: имя метки, упомянутое в теле ЛЮБОГО другого блока (или в своём, но не в объявлении)
refs = {b[0]: set() for b in blocks}
word = re.compile(r'[A-Za-z_][\w]*')
for i, b in enumerate(blocks):
    for ln in b[4]:
        ln = re.sub(r'^[A-Za-z_][\w]*:', '', ln)       # своё объявление не считаем
        for w in word.findall(ln):
            if w in refs and w != b[0]:
                refs[w].add(i)
            elif w in refs and w == b[0]:
                refs[w].add(i)                          # рекурсия/цикл внутри себя — ссылка на себя

cov = None
if COV:
    cov = open(COV, 'rb').read()

# достижимость: блок живой, если на него есть ссылка ИЗ ЖИВОГО блока или провал из живого.
# Корни — блоки, которые исполнялись (по покрытию), либо (без покрытия) вообще все.
live = set()
if cov:
    for i, b in enumerate(blocks):
        if any(cov[a] for a in range(b[1], b[1] + b[2])):
            live.add(i)
else:
    live = set(range(len(blocks)))
changed = True
while changed:
    changed = False
    for i, b in enumerate(blocks):
        if i in live:
            continue
        src = refs[b[0]] - {i}
        if (src & live) or (b[3] and i > 0 and (i - 1) in live):
            live.add(i)
            changed = True

print("блок                                адрес  байт  ссылок  холодный  вердикт")
dead = cold_live = 0
for i, b in enumerate(blocks):
    if not b[2]:
        continue
    nref = len(refs[b[0]] - {i})
    hot = cov is None or any(cov[a] for a in range(b[1], b[1] + b[2]))
    if hot and not ALL:
        continue
    kind = 'данные, ' if b[5] else ''
    verdict = kind + ('ЖИВОЙ (достижим)' if i in live else
                      ('МЁРТВ: нет ссылок' if nref == 0 and not b[3] else 'МЁРТВ: ссылки только из мёртвых'))
    if i not in live:
        dead += b[2]
    elif not hot:
        cold_live += b[2]
    print(f"{b[0]:34} {b[1]:04x} {b[2]:5} {nref:6}  {'нет' if hot else 'ДА ':8} {verdict}")
print(f"\nмёртвых байт: {dead}; холодных, но достижимых: {cold_live}")
