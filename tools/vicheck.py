#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сверка журнала OUT в ВИ53 (tools/aytrace_vec.js --vi=) с зеркалом регистров AY.

    python3 tools/vicheck.py <журнал_ВИ53> <журнал_регистров_AY> [ключи]
      --first=N          показать первые N нот (по умолчанию 12)
      --expect=P,P,...   потребовать, чтобы среди делителей встретились 13.5*P для каждого P

Проверяем то, что слышно: каждый делитель, залитый в счётчик в режиме 3, обязан
соответствовать периоду, который драйвер в тот же момент держит в зеркале регистров AY.
  частота ВИ53 = 1497600 / делитель
  частота AY   = 1775000 / (16 * период)
Расхождение должно быть <= 1 % (математически 13.5 даёт 0.004 %, остальное — округление
делителя до целого: 13P + P/2).

Порты Вектора ПЕРЕСТАВЛЕНЫ: управление 0x08, счётчики 0/1/2 = 0x0B/0x0A/0x09.
"""
import sys

CLK_VI = 1497600           # такт таймера
CLK_AY = 1775000           # такт AY Вектора (делится на 16 внутри чипа)
PORT_CTR = {0x0B: 0, 0x0A: 1, 0x09: 2}


def load(path):
    out = []
    for line in open(path):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        out.append(tuple(int(x) for x in line.split()))
    return out


def vi_notes(vilog):
    """журнал OUT -> [(кадр, счётчик, делитель)] и [(кадр, счётчик, режим)] для управляющих слов."""
    half = {0: None, 1: None, 2: None}     # накопленный младший байт
    mode = {0: None, 1: None, 2: None}
    notes, ctrls = [], []
    for f, port, val in vilog:
        if port == 0x08:
            ctr = val >> 6
            if ((val >> 4) & 3) == 0:
                continue                    # команда защёлки (vec_di_budget читает остаток)
            if ctr > 2:
                continue
            mode[ctr] = (val >> 1) & 7
            half[ctr] = None                # управляющее слово перезапускает счётчик
            ctrls.append((f, ctr, mode[ctr]))
        elif port in PORT_CTR:
            ctr = PORT_CTR[port]
            if half[ctr] is None:
                half[ctr] = val
            else:
                notes.append((f, ctr, half[ctr] | (val << 8), mode[ctr]))
                half[ctr] = None
    return notes, ctrls


def ay_snapshots(aylog):
    """журнал записей регистров -> [(кадр, [период A, B, C])] ПОСЛЕ каждой записи.

    Снимок нужен после каждой записи, а не на границе кадра: эффекты меняют период по 40 раз
    за игровую итерацию, и к концу кадра в зеркале лежит уже не то, что ISR слил в счётчик.
    """
    regs = [0] * 16
    snaps = []
    for f, r, v in aylog:
        regs[r] = v
        snaps.append((f, (regs[0] | ((regs[1] & 0x0F) << 8),
                          regs[2] | ((regs[3] & 0x0F) << 8),
                          regs[4] | ((regs[5] & 0x0F) << 8))))
    return snaps


def main():
    vif, ayf = sys.argv[1], sys.argv[2]
    opt = {a.split('=')[0]: a.split('=', 1)[1] for a in sys.argv[3:] if a.startswith('--')}
    first = int(opt.get('--first', 12))
    expect = [int(x) for x in opt['--expect'].split(',')] if '--expect' in opt else []

    notes, ctrls = vi_notes(load(vif))
    snaps = ay_snapshots(load(ayf))
    f_first = snaps[0][0] if snaps else 0

    def match(f, div):
        """какой канал в кадрах [f-1, f] держал период, дающий этот делитель"""
        for fr, pers in snaps:
            if fr < f - 1:
                continue
            if fr > f:
                break
            for i, p in enumerate(pers):
                if p and 13 * p + (p >> 1) == div:
                    return i, p
        return None, None

    print('делителей залито %d, управляющих слов %d' % (len(notes), len(ctrls)))
    print('%-6s %-5s %-8s %-10s %-4s %-7s %-10s %s' %
          ('кадр', 'счёт', 'делитель', 'f ВИ53,Гц', 'кан', 'период', 'f AY,Гц', 'ошибка'))
    bad, worst, shown = 0, 0.0, 0
    seen_div = set()
    for f, ctr, div, mode in notes:
        seen_div.add(div)
        if mode != 3:
            continue                        # счётчик 0 в режиме 0 — секундомер бюджета di, не нота
        fvi = CLK_VI / div if div else 0.0
        if f < f_first:
            continue                        # прогрев стенда: записей в зеркало ещё не снимали
        ch, p = match(f, div)
        if ch is None:
            bad += 1
            if shown < first:
                print('%-6d %-5d %-8d %-10.1f %-4s %-7s %-10s НЕТ ПЕРИОДА В ЗЕРКАЛЕ' %
                      (f, ctr, div, fvi, '-', '-', '-'))
                shown += 1
            continue
        fay = CLK_AY / (16 * p)
        err = abs(fvi - fay) / fay * 100
        worst = max(worst, err)
        if err > 1.0:
            bad += 1
        if shown < first:
            print('%-6d %-5d %-8d %-10.1f %-4s %-7d %-10.1f %.3f %%' %
                  (f, ctr, div, fvi, 'ABC'[ch], p, fay, err))
            shown += 1
    print('макс. расхождение частоты %.3f %% (порог 1 %%), делителей без периода в зеркале: %d' % (worst, bad))
    for p in expect:
        d = 13 * p + (p >> 1)
        ok = d in seen_div
        print('  ожидался период %3d -> делитель %5d (%.1f Гц): %s'
              % (p, d, CLK_VI / d, 'ЕСТЬ' if ok else 'НЕТ'))
        if not ok:
            bad += 1
    print('ИТОГ: %s' % ('ПОЛНОЕ СОВПАДЕНИЕ' if bad == 0 else 'ПРОБЛЕМ %d' % bad))
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
