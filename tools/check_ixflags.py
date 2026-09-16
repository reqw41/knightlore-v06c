#!/usr/bin/env python3
"""Гейт: ни один хелпер (ix+d)/(iy+d), который ПОРТИТ перенос, не должен стоять перед его чтением.

Почему это отдельный гейт. Хелперы il_*_inc/dec/bit считают адрес ячейки как
`add a,l / adc a,0` — и тем самым выставляют CY по переносу из младшего байта адреса.
У Z80 `inc (ix+d)`, `dec (ix+d)` и `bit n,(ix+d)` перенос НЕ ТРОГАЮТ. Если после такого
хелпера код читает CY (jp/jr/ret c|nc, adc, sbc, rla, rra), он получает мусор — тихо,
без единого предупреждения ассемблера.

Так уже случилось: в upd_103 (объект «лишняя жизнь») между `call is_on_or_near_obj` и
`jr NC` стояли `dec (ix+4)` и `dec (ix+5)`; перенос после хелпера всегда был нулевым
(0xE8+5 без переноса), и лишняя жизнь не выдавалась НИКОГДА.

Хелперы-загрузки/записи (lda/sta/stn/set/res/...) флаги восстанавливают целиком и
здесь не считаются; ALU-хелперы (add/sub/cp/or/...) перенос обязаны менять — они тоже
не считаются.

  python3 tools/check_ixflags.py build/knightlore_vec.asm [ещё...]
"""
import re
import sys

BAD = {'il_ix_inc', 'il_ix_dec', 'il_ix_bit', 'il_iy_inc', 'il_iy_dec', 'il_iy_bit'}
# инструкции, ЗАПИСЫВАЮЩИЕ перенос (после них мусор от хелпера уже неважен)
CY_W = re.compile(r'^(add|adc|sub|sbc|and|or|xor|cp|rlca|rrca|rla|rra|scf|ccf|neg|daa)\b', re.I)
# инструкции, ЧИТАЮЩИЕ перенос
CY_R = re.compile(r'^(jp|jr|ret|call)\s+(n?c)\b|^(adc|sbc|rla|rra)\b', re.I)
STOP = re.compile(r'^(jp|jr|ret|call)\b', re.I)


def body(line):
    return re.sub(r'^[A-Za-z_.@][\w.@]*:\s*', '', line.split(';')[0].strip())


hits = 0
for path in sys.argv[1:]:
    lines = open(path, encoding='latin-1').read().split('\n')
    for i, raw in enumerate(lines):
        # компактная форма зовётся `call il_xxx`, а самые частые — однобайтовым `rst il_xxx`
        # (RST-вектор, §31.1): гейт обязан видеть обе записи, иначе он молча ослепнет
        m = re.match(r'^(?:call|rst)\s+(il_[a-z_0-9]+)', body(raw))
        if not m or m.group(1) not in BAD:
            continue
        for j in range(i + 1, min(i + 24, len(lines))):
            t = body(lines[j])
            if not t or t.lower().startswith('db'):
                continue                      # байт смещения хелпера / пустая строка
            if CY_R.match(t):
                hits += 1
                print("  %s:%d: %s портит перенос, а через %d команд его читают: %s"
                      % (path, i + 1, m.group(1), j - i, t))
                break
            if CY_W.match(t) or STOP.match(t):
                break
print("check_ixflags: чтений испорченного переноса %d" % hits)
sys.exit(1 if hits else 0)
