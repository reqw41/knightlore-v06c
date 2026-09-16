#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""zx0pack.py — упаковщик в формат ZX0 (v2, «вперёд») на чистом Python.

Формат придуман Эйнаром Саукасом (github.com/einar-saukas/ZX0), здесь — свой упаковщик,
чтобы сборка не тянула внешний инструмент, и свой распаковщик для сверки.
Распаковщик на i8080 — в tools/mkrelease.py (RUNNER), гейт tools/packgate_vec.js сверяет одно с другим.

Формат (три вида блоков; бит-поток и байты чередуются в одном потоке):
    литералы             0  Elias(длина)  байты...
    копия с ПРОШЛЫМ off  0  Elias(длина)               (только сразу после литералов)
    копия с НОВЫМ off    1  Elias(MSB)  LSB  Elias(длина-1)
Elias — «переслоенный» гамма-код: маркер(0), бит, маркер(0), бит, ..., маркер(1).
MSB = (off-1)//128+1 пишется с ИНВЕРТИРОВАННЫМИ битами данных; LSB = (127-(off-1)%128)<<1,
причём первый бит Elias(длина-1) кладётся в бит 0 этого же байта. Конец потока — MSB = 256.
Первый блок всегда литеральный, его бит-признак не пишется. Начальное «прошлое смещение» = 1.

Разбор — динамика по позициям с двумя состояниями (последний блок литеральный / копия).
Кандидатов на смещение даёт хеш-цепочка по трём байтам, а не полный перебор, как у
эталонного оптимизатора, поэтому поток выходит на несколько процентов длиннее эталона
(числа — docs/PORT_RECIPE.md §33). Блок «копия с прошлым смещением» ставится при выводе.

    python3 tools/zx0pack.py <вход> <выход.zx0>
"""

import sys

MAX_OFFSET = 32640          # предел формата (256*128 - 128)
CANDIDATES = 64             # сколько звеньев хеш-цепочки смотреть на позицию
# Виртуальная цена блока в битах. Ноль даёт самый короткий поток, но блоков вдвое больше,
# а распаковщик тратит на блок ~600 тактов Вектора против ~39 на байт копии: время
# распаковки определяется ЧИСЛОМ БЛОКОВ, а не длиной потока. Замер на образе игры
# (docs/PORT_RECIPE.md §33): 0 бит — 24 869 Б и 9 079 блоков, 8 бит — 27 468 Б и 3 890,
# 16 бит — 30 722 Б и 1 628. Взято 8: в нижние 32К влезает с запасом, а ждать вдвое меньше.
BLOCK_COST = 10
NEAR_LEN = 64               # длины совпадения, перебираемые подряд
ELIAS_MAX_BITS = 33


def elias_bits(v):
    n = 1
    while v > 1:
        v >>= 1
        n += 2
    return n


def _match_len(data, p, i, lim):
    """длина совпадения data[p:] и data[i:], не длиннее lim — сравнением срезов (быстро)"""
    l = 0
    step = 8
    while l + step <= lim and data[p + l:p + l + step] == data[i + l:i + l + step]:
        l += step
        if step < 512:
            step <<= 1
    step >>= 1
    while step:
        if l + step <= lim and data[p + l:p + l + step] == data[i + l:i + l + step]:
            l += step
        step >>= 1
    return l


def find_matches(data):
    """позиция -> [(смещение, длина)], только «рекордные» длины (меньшее смещение дешевле)"""
    n = len(data)
    heads = {}
    prev = [-1] * n
    out = [()] * n
    for i in range(n):
        res = []
        lim = n - i
        if i > 0 and data[i] == data[i - 1]:
            res.append((1, _match_len(data, i - 1, i, lim)))
        if i + 2 < n:
            key = data[i:i + 3]
            p = heads.get(key, -1)
            cnt = 0
            best = res[0][1] if res else 2
            while p >= 0 and cnt < CANDIDATES:
                off = i - p
                if off > MAX_OFFSET:
                    break
                l = _match_len(data, p, i, lim)
                if l > best:
                    best = l
                    res.append((off, l))
                    if l >= 1024:
                        break
                p = prev[p]
                cnt += 1
            prev[i] = heads.get(key, -1)
            heads[key] = i
        out[i] = tuple(res)
    return out


def parse(data):
    """разбор на блоки: [('lit', L) | ('mat', L, off), ...] — один проход слева направо.

    Два состояния на позицию: последний блок литеральный (fL) или копия (fM).
    Литеральные блоки не идут подряд (иначе поток их бы склеил), поэтому литералы
    считаются только от fM, а копия — от лучшего из двух.
    Цена литерального блока длины L: 1 + elias(L) + 8L; копии: 1 + elias(MSB) + 8 + elias(L-1).
    Минимум по началу литерального блока берётся по корзинам elias монотонной очередью:
    в корзине k длины 2^k..2^(k+1)-1 стоят одинаковые 2k+1 бит, а 8L выносится
    заменой g[i] = fM[i] - 8i.
    """
    from collections import deque
    n = len(data)
    INF = float('inf')
    matches = find_matches(data)
    fL = [INF] * (n + 1)
    fM = [INF] * (n + 1)
    bL = [None] * (n + 1)
    bM = [None] * (n + 1)
    offM = [0] * (n + 1)          # смещение копии, которой путь пришёл в позицию
    fM[0] = 0                     # старт: дальше обязателен литеральный блок
    nb = 1
    while (1 << nb) <= n:
        nb += 1
    dqs = [deque() for _ in range(nb + 1)]
    head = [0] * (nb + 1)

    for j in range(0, n + 1):
        # 1. fM[j] уже окончателен (его правили копии из позиций < j); fL[j] считаем сейчас
        if j:
            best = INF
            arg = None
            for k in range(nb + 1):
                hi = j - (1 << k)
                if hi < 0:
                    break
                lo = j - ((1 << (k + 1)) - 1)
                dq = dqs[k]
                while head[k] <= hi:
                    i = head[k]
                    head[k] += 1
                    gi = fM[i] - 8 * i if fM[i] < INF else INF
                    if gi < INF:
                        while dq and dq[-1][1] >= gi:
                            dq.pop()
                        dq.append((i, gi))
                while dq and dq[0][0] < lo:
                    dq.popleft()
                if dq:
                    c = dq[0][1] + 8 * j + 1 + (2 * k + 1) + BLOCK_COST
                    if c < best:
                        best = c
                        arg = dq[0][0]
            fL[j] = best
            bL[j] = arg
        # 2. копии, начинающиеся в позиции j (j >= 1: первый блок обязан быть литеральным)
        if j >= 1 and j < n:
            best = fL[j] if fL[j] < fM[j] else fM[j]
            if best == INF:
                continue
            # 2а. «копия с ПРОШЛЫМ смещением» — только сразу после литералов, зато без
            # цены смещения (1 + elias(L) бит). Прошлое смещение берётся у пути, каким
            # динамика пришла в j: точным его сделать нельзя (состояние (позиция, смещение)
            # не помещается), поэтому это эвристика — поток от неё только короче.
            if fL[j] < INF and bL[j] is not None:
                ro = offM[bL[j]]
                if ro and ro <= j:
                    ml = _match_len(data, j - ro, j, n - j)
                    if ml >= 1:
                        top = ml if ml < NEAR_LEN else NEAR_LEN
                        ls = list(range(1, top + 1))
                        if ml > NEAR_LEN:
                            ls += [ml, ml - 1, ml // 2]
                        for l in ls:
                            if l < 1 or l > ml:
                                continue
                            c = fL[j] + 1 + elias_bits(l) + BLOCK_COST
                            if c < fM[j + l]:
                                fM[j + l] = c
                                bM[j + l] = (l, ro, j, 'L')
                                offM[j + l] = ro
            st = 'L' if fL[j] < fM[j] else 'M'
            for off, maxlen in matches[j]:
                base = best + 1 + elias_bits((off - 1) // 128 + 1) + 8 + BLOCK_COST
                top = maxlen if maxlen < NEAR_LEN else NEAR_LEN
                for l in range(2, top + 1):
                    c = base + elias_bits(l - 1)
                    if c < fM[j + l]:
                        fM[j + l] = c
                        bM[j + l] = (l, off, j, st)
                        offM[j + l] = off
                if maxlen > NEAR_LEN:
                    ls = {maxlen, maxlen - 1, maxlen // 2}
                    p = 128
                    while p <= maxlen:
                        ls.add(p)
                        ls.add(p - 1)
                        p <<= 1
                    for l in ls:
                        if NEAR_LEN < l <= maxlen:
                            c = base + elias_bits(l - 1)
                            if c < fM[j + l]:
                                fM[j + l] = c
                                bM[j + l] = (l, off, j, st)
                                offM[j + l] = off

    blocks = []
    j = n
    state = 'M' if fM[n] <= fL[n] else 'L'
    while j > 0:
        if state == 'L':
            i = bL[j]
            blocks.append(('lit', j - i))
            j = i
            state = 'M'
        else:
            l, off, i, st = bM[j]
            blocks.append(('mat', l, off))
            j = i
            state = st
    blocks.reverse()
    assert blocks and blocks[0][0] == 'lit', 'первый блок обязан быть литеральным'
    return blocks


class BitWriter:
    def __init__(self):
        self.out = bytearray()
        self.bit_index = -1
        self.bit_mask = 0
        self.backtrack = True

    def byte(self, v):
        self.out.append(v & 0xFF)

    def bit(self, v):
        if self.backtrack:
            if v:
                self.out[-1] |= 1
            self.backtrack = False
            return
        if not self.bit_mask:
            self.bit_mask = 128
            self.bit_index = len(self.out)
            self.out.append(0)
        if v:
            self.out[self.bit_index] |= self.bit_mask
        self.bit_mask >>= 1

    def elias(self, value, invert=False):
        i = 1
        while (i << 1) <= value:
            i <<= 1
        while i > 1:
            i >>= 1
            self.bit(0)
            b = 1 if (value & i) else 0
            self.bit(1 - b if invert else b)
        self.bit(1)


def _emit(data, blocks, w, on_write=None, on_read=None):
    pos = 0
    last_offset = 1
    prev_lit = False
    for b in blocks:
        n0 = len(w.out)
        if b[0] == 'lit':
            L = b[1]
            w.bit(0)
            w.elias(L)
            if on_write:
                on_write(len(w.out) - n0)
            for k in range(L):
                w.byte(data[pos + k])
                if on_write:
                    on_write(1)
                if on_read:
                    on_read(1)
            pos += L
            prev_lit = True
        else:
            _, l, off = b
            if off == last_offset and prev_lit:
                w.bit(0)
                w.elias(l)
            else:
                w.bit(1)
                w.elias((off - 1) // 128 + 1, invert=True)
                w.byte((127 - (off - 1) % 128) << 1)
                w.backtrack = True
                w.elias(l - 1)
                last_offset = off
            if on_write:
                on_write(len(w.out) - n0)
            if on_read:
                on_read(l)
            pos += l
            prev_lit = False
    n0 = len(w.out)
    w.bit(1)
    w.elias(256, invert=True)
    if on_write:
        on_write(len(w.out) - n0)
    return bytes(w.out)


def compress(data):
    """-> (поток, delta). delta — на сколько байт поток обязан КОНЧАТЬСЯ выше конца
    распакованных данных, чтобы распаковка «на месте» не затёрла непрочитанное."""
    blocks = parse(data)
    out = _emit(data, blocks, BitWriter())
    st = {'diff': len(out) - len(data), 'delta': 0}

    def wrote(n):
        st['diff'] -= n

    def read(n):
        st['diff'] += n
        if st['delta'] < st['diff']:
            st['delta'] = st['diff']

    _emit(data, blocks, BitWriter(), wrote, read)
    return out, st['delta']


# ----------------------------------------------------------------------------
# распаковщик (сверка; тот же порядок действий, что у i8080-версии)
# ----------------------------------------------------------------------------

class BitReader:
    def __init__(self, data):
        self.d = data
        self.p = 0
        self.acc = 0x80          # маркер: после сдвига A==0 => резервуар пуст

    def byte(self):
        v = self.d[self.p]
        self.p += 1
        return v

    def bit(self):
        a = (self.acc << 1) & 0xFF
        cy = (self.acc >> 7) & 1
        if a == 0:
            v = self.byte()
            a = ((v << 1) & 0xFF) | cy       # старый CY (маркер) въезжает снизу
            cy = (v >> 7) & 1
        self.acc = a
        return cy

    def elias(self, invert=False):
        v = 1
        while True:
            if self.bit():
                return v
            b = self.bit()
            v = (v << 1) | ((1 - b) if invert else b)


def decompress(data):
    r = BitReader(data)
    out = bytearray()
    off = 1
    state = 'lit'
    while True:
        if state == 'lit':
            for _ in range(r.elias()):
                out.append(r.byte())
            state = 'new' if r.bit() else 'last'
        elif state == 'last':
            n = r.elias()
            for _ in range(n):
                out.append(out[-off])
            state = 'new' if r.bit() else 'lit'
        else:
            msb = r.elias(invert=True)
            if msb == 256:
                return bytes(out)
            lsb = r.byte()
            off = msb * 128 - (lsb >> 1)
            v = 1
            marker = lsb & 1
            while not marker:
                v = (v << 1) | r.bit()
                marker = r.bit()
            for _ in range(v + 1):
                out.append(out[-off])
            state = 'new' if r.bit() else 'lit'


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    data = open(sys.argv[1], 'rb').read()
    out, delta = compress(data)
    assert decompress(out) == data, 'распаковка не совпала с исходником'
    open(sys.argv[2], 'wb').write(out)
    print('%s: %d -> %d байт (%.1f %%), delta %d' %
          (sys.argv[1], len(data), len(out), 100.0 * len(out) / len(data), delta))
    return 0


if __name__ == '__main__':
    sys.exit(main())
