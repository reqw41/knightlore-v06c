#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Рендер журнала записей в ВИ53 (tools/aytrace_vec.js --vi=) в WAV — слушать без эмулятора.

    python3 tools/vi2wav.py <журнал_ВИ53> [--wav=файл.wav] [--notes] [--fps=50.08] [--sr=44100]

Журнал: строки «кадр порт значение», порт 0x08 — управляющее слово, 0x0B/0x0A/0x09 — счётчики 0/1/2.
Восстанавливается ровно то, что слышно на железе:
  * управляющее слово «режим 3, LSB+MSB» задаёт счётчику меандр и ПЕРЕЗАПУСКАЕТ его;
  * пара байт (сперва младший, потом старший) заливает делитель — тон 1497600/делитель;
  * управляющее слово БЕЗ последующей пары = счётчик встал (тишина) — так драйвер гасит голос.
Время события — начало его кадра (журнал точнее кадра не знает): кадр = 1/FPS,
FPS по умолчанию 50.08 (59904 такта на кадр при 3 МГц, как в aytrace_vec.js).

--notes печатает поканальный список нот: кадр, делитель, частота, длина в кадрах — им же меряется,
сколько ступенек короткого эффекта дошло до счётчика (docs/PORT_RECIPE.md §20, §23).
"""
import struct
import sys

CLK_VI = 1497600.0
PORT_CTR = {0x0B: 0, 0x0A: 1, 0x09: 2}


def load(path):
    ev = []
    for line in open(path):
        line = line.split('#')[0].strip()
        if not line:
            continue
        p = line.split()
        if len(p) < 3:
            continue
        ev.append((int(p[0]), int(p[1], 0), int(p[2], 0)))
    return ev


def decode(ev):
    """-> {счётчик: [(кадр_начала, кадр_конца, делитель)]}; делитель None = тишина."""
    notes = {0: [], 1: [], 2: []}
    half = {}          # счётчик -> ожидается старший байт делителя, накопленный младший
    cur = {0: None, 1: None, 2: None}      # текущий делитель
    since = {0: 0, 1: 0, 2: 0}
    mode = {0: 0, 1: 0, 2: 0}              # режим счёта: звучит только меандр (режим 3)
    last_frame = 0

    def put(c, frame, div):
        if cur[c] == div:
            return
        if cur[c] is not None:
            notes[c].append((since[c], frame, cur[c]))
        cur[c] = div
        since[c] = frame

    for frame, port, val in ev:
        last_frame = frame
        if port == 0x08:
            c = (val >> 6) & 3
            if c > 2:
                continue
            half.pop(c, None)
            mode[c] = (val >> 1) & 7
            put(c, frame, None)            # управляющее слово останавливает счёт до заливки делителя
        else:
            c = PORT_CTR.get(port)
            if c is None:
                continue
            if c in half:
                d = half.pop(c) | (val << 8)
                # режим 0 = секундомер бюджета di (счётчик 0 в игре): не звучит, в ноты не идёт
                put(c, frame, d if mode[c] == 3 else None)
            else:
                half[c] = val
    for c in range(3):
        if cur[c] is not None:
            notes[c].append((since[c], last_frame + 1, cur[c]))
    return notes


def render(notes, fps, sr, amp=0.28):
    end = max([n[1] for c in notes for n in notes[c]] + [1])
    ns = int(end / fps * sr) + sr // 10
    buf = [0.0] * ns
    for c in range(3):
        phase = 0.0
        for f0, f1, div in notes[c]:
            if not div:
                continue
            freq = CLK_VI / div
            i0, i1 = int(f0 / fps * sr), int(f1 / fps * sr)
            step = freq / sr
            for i in range(i0, min(i1, ns)):
                phase += step
                if phase >= 1.0:
                    phase -= 1.0
                buf[i] += amp if phase < 0.5 else -amp
    return buf


def wav(path, buf, sr):
    pcm = bytearray()
    for v in buf:
        s = int(max(-1.0, min(1.0, v)) * 32767)
        pcm += struct.pack('<h', s)
    with open(path, 'wb') as f:
        f.write(b'RIFF' + struct.pack('<I', 36 + len(pcm)) + b'WAVEfmt ')
        f.write(struct.pack('<IHHIIHH', 16, 1, 1, sr, sr * 2, 2, 16))
        f.write(b'data' + struct.pack('<I', len(pcm)))
        f.write(pcm)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    src = args[0]
    opt = lambda n, d=None: next((a.split('=', 1)[1] for a in args if a.startswith('--' + n + '=')), d)
    fps = float(opt('fps', '50.08'))
    sr = int(opt('sr', '44100'))
    notes = decode(load(src))
    tot = sum(1 for c in notes for n in notes[c] if n[2])
    print('%s: нот (залитых делителей) %d — счётчик 0: %d, 1: %d, 2: %d'
          % (src, tot, *[sum(1 for n in notes[c] if n[2]) for c in range(3)]))
    if '--notes' in args:
        for c in range(3):
            for f0, f1, div in notes[c]:
                if div:
                    print('  счётчик %d  кадр %5d..%-5d  делитель %5d  %8.1f Гц  %d кадр(ов)'
                          % (c, f0, f1, div, CLK_VI / div, f1 - f0))
    w = opt('wav')
    if w:
        wav(w, render(notes, fps, sr), sr)
        print('WAV: %s (%.2f с, %d Гц)' % (w, max([n[1] for c in notes for n in notes[c]] + [1]) / fps, sr))
    return 0


if __name__ == '__main__':
    sys.exit(main())
