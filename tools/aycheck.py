#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сверка журнала записей в AY (tools/aytrace_vec.js) с эталонной моделью MSX.

    python3 tools/aycheck.py <журнал.txt> <мелодия> [<кадр старта>] [--mode=a,b,c]

Мелодия: start_game | game_over | game_complete | menu | blip.
--mode: режимы громкости каналов 0,1,2, оставшиеся от ПРЕДЫДУЩЕЙ мелодии (play_tune их не
обнуляет — дефект оригинала, SOUND.md). Нужен только game_complete: её потоки начинаются
сразу с ноты. Играла menu -> --mode=0,1,2; играла start_game -> --mode=0,2,2.
«Кадр старта» — кадр, в котором Вектор записал список инициализации PSG
(R8=0 R9=0 R10=0 R7=0x38 R11=0 R12=8); первый тик мелодии приходится на следующий кадр.
Без него берётся ПЕРВЫЙ кусок журнала, длина которого совпала с моделью просимой мелодии
(а если такого нет — первый кусок вообще, и сверка краснеет «НЕ ТА МЕЛОДИЯ»).

ОПОЗНАНИЕ МЕЛОДИИ (§28). Журнал режется на куски по собственным границам мелодии: начало —
список инициализации PSG, конец — четвёрка глушения R7=0xBF R8=0 R9=0 R10=0. Длина куска
сравнивается с длиной модели ДО поканальной сверки. Без этого сверщик молча брал первый
попавшийся кусок и первые len(модель) записей из него: журнал с мелодией МЕНЮ, сверенный
с моделью start_game, давал «76 расхождений» — число, которое не значит ничего.

Эталон — msx_psg_model.py из ~/projects/knightlore-research/msx (проверен поэлементно
против прогона настоящего кода игры на Z80-интерпретаторе, verify_log.txt).

Сравнение поканальное: у MSX проигрыватель обходит каналы по разу за «проход»,
у нас за кадр каждый канал отматывает свои 114 проходов подряд — глобальный порядок
записей поэтому разный, а последовательность записей ВНУТРИ канала обязана совпадать
байт в байт, и кадр каждой записи — совпадать с floor(проход/114) в пределах +-1.
"""
import os
import sys

MSXDIR = os.path.expanduser('~/projects/knightlore-research/msx')
sys.path.insert(0, MSXDIR)
os.chdir(MSXDIR)
import msx_psg_model as M   # noqa: E402

HEADERS = {'start_game': 0x46A4, 'game_over': 0x46AA, 'game_complete': 0x46B0,
           'menu': 0x46B6, 'blip': 0x46C2}
PASSES_PER_FRAME = 114

# регистр -> канал проигрывателя (0 = слот 0/AY C, 1 = слот 1/AY B, 2 = слот 2/AY A)
REG_CH = {4: 0, 5: 0, 10: 0, 2: 1, 3: 1, 9: 1, 0: 2, 1: 2, 8: 2}


def split(writes, key):
    """writes = [(время, рег, знач)] -> {канал: [(время, рег, знач)]}, 'L' = списки (R7/R11/R12/R13)."""
    out = {0: [], 1: [], 2: [], 'L': []}
    for t, r, v in writes:
        out[REG_CH.get(r, 'L')].append((t, r, v))
    return out


INIT_PAT = [(8, 0), (9, 0), (10, 0), (7, 0x38), (11, 0), (12, 8)]   # список инициализации PSG
STOP_REGS = [7, 8, 9, 10]                                          # глушение: R7=0xBF, громкости 0
STOP_VALS = [None, 0, 0, 0]                                        # им кончается КАЖДАЯ из пяти мелодий


def segments(raw):
    """Куски журнала «мелодия целиком»: [(индекс начала, кадр начала, число записей, кадр конца)].

    Начало — список инициализации PSG, конец — четвёрка глушения включительно. Кусок без
    глушения (мелодия ещё играет в конце прогона) даёт длину до конца журнала и конец None.
    """
    out = []
    i = 0
    while i < len(raw) - 6:
        if [(r, v) for _, r, v in raw[i:i + 6]] != INIT_PAT:
            i += 1
            continue
        end = None
        j = i + 6
        while j < len(raw) - 3:
            if ([r for _, r, _ in raw[j:j + 4]] == STOP_REGS
                    and [v for _, _, v in raw[j + 1:j + 4]] == [0, 0, 0]):
                end = j + 4
                break
            j += 1
        out.append((i, raw[i][0], (end if end is not None else len(raw)) - i,
                    raw[end - 1][0] if end is not None else None))
        i = end if end is not None else len(raw)
    return out


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith('--')]
    modes = next((a.split('=', 1)[1] for a in sys.argv[1:] if a.startswith('--mode=')), None)
    logf, name = argv[0], argv[1]
    after = int(argv[2]) if len(argv) > 2 else None
    p = M.Player(HEADERS[name])
    if modes:
        # Поле «режим громкости» канала MSX play_tune НЕ обнуляет (SOUND.md, «Замечание о дефекте»):
        # потоки game_complete и мёртвой 0x46BC начинаются сразу с ноты, без команды режима, и
        # звучат громкостью, оставшейся от предыдущей мелодии. Модель играет мелодию в ИЗОЛЯЦИИ и
        # стартует с нулей, поэтому для таких мелодий ей надо сказать, что играло перед ними.
        p.mode = [int(x) for x in modes.split(',')]
    p.run()
    ref = split(p.writes, None)

    raw = []
    for line in open(logf):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        a, b, c = line.split()
        raw.append((int(a), int(b), int(c)))
    # порезать журнал на куски «мелодия целиком» и ОПОЗНАТЬ нужный по длине куска (§28)
    segs = segments(raw)
    if not segs:
        print('в журнале нет ни одной мелодии (не найден список инициализации PSG)')
        return 1
    print('кусков «мелодия целиком» в журнале: %d' % len(segs))
    for i0, fs, ln, fe in segs:
        print('  запись %4d, кадры %4d..%-5s записей %4d%s'
              % (i0, fs, ('%d' % fe) if fe is not None else '?', ln,
                 '' if fe is not None else '   (глушения нет — мелодия не кончилась в журнале)'))
    if after is None:
        cand = [s for s in segs if s[2] == len(p.writes)]
        seg = cand[0] if cand else segs[0]
    else:
        cand = [s for s in segs if s[1] >= after]
        if not cand:
            print('в журнале нет мелодии, начинающейся с кадра %d или позже' % after)
            return 1
        seg = cand[0]
    start, f0, seg_len = seg[0], seg[1], seg[2]
    if seg_len != len(p.writes):
        # СТОП ДО СВЕРКИ: иначе сверщик возьмёт первые len(модель) записей чужой мелодии и
        # напечатает осмысленное на вид число расхождений, которое не значит ничего (§28).
        lens = {}
        for n, h in HEADERS.items():
            q = M.Player(h)
            q.run()
            lens[n] = len(q.writes)
        who = [n for n, l in lens.items() if l == seg_len]
        print('НЕ ТА МЕЛОДИЯ: просили %s (модель %d записей), а в куске журнала %d.'
              % (name, len(p.writes), seg_len))
        if who:
            print('  по длине это %s. Длины моделей: %s'
                  % (' или '.join(who), ', '.join('%s %d' % kv for kv in sorted(lens.items()))))
        else:
            print('  ни одна из пяти моделей такой длины не даёт (мелодия оборвана клавишей'
                  ' или журнал кончился раньше). Длины моделей: %s'
                  % ', '.join('%s %d' % kv for kv in sorted(lens.items())))
        print('  Стенд: клавишу нажимать ПОСЛЕ старта цикла меню (кадр >= 90; проверено 200),'
              ' иначе нажатие пропадает и в журнале остаётся только мотив меню.')
        print('ИТОГ: НЕ ТА МЕЛОДИЯ')
        return 1
    # берём ровно столько записей, сколько даёт модель: любая посторонняя запись,
    # вклинившаяся в мелодию, сразу проявится расхождением значения
    got_all = raw[start:start + len(p.writes)]
    got = split(got_all, None)

    print('мелодия %s, заголовок 0x%04X; проходов в модели %d (= %.1f кадра)'
          % (name, HEADERS[name], p.passno, p.passno / PASSES_PER_FRAME))
    print('старт в кадре %d (запись %d журнала); записей: модель %d, взято %d'
          % (f0, start, len(p.writes), len(got_all)))
    nxt = raw[start + len(p.writes)] if start + len(p.writes) < len(raw) else None
    if nxt:
        print('следующая запись после мелодии: кадр %d, R%d=%d' % nxt)
    bad = 0
    for ch in (0, 1, 2, 'L'):
        r, gt = ref[ch], got[ch]
        nm = 'канал %s' % ch if ch != 'L' else 'списки (R7/R11/R12/R13)'
        if len(r) != len(gt):
            print('  %-26s ЧИСЛО ЗАПИСЕЙ РАЗНОЕ: модель %d, Вектор %d' % (nm, len(r), len(gt)))
            bad += abs(len(r) - len(gt))
        n = min(len(r), len(gt))
        mism_v = 0        # расхождения по (регистр, значение)
        mism_t = 0        # расхождения по кадру больше чем на 1
        worst = 0
        for i in range(n):
            pt, pr, pv = r[i]
            gtf, gr, gv = gt[i]
            if (pr, pv) != (gr, gv):
                if mism_v < 5:
                    print('    %s: запись %d — модель R%d=%d, Вектор R%d=%d' % (nm, i, pr, pv, gr, gv))
                mism_v += 1
                continue
            exp = f0 + 1 + max(pt, 0) // PASSES_PER_FRAME if pt >= 0 else f0
            d = gtf - exp
            worst = max(worst, abs(d))
            if abs(d) > 1:
                if mism_t < 5:
                    print('    %s: запись %d (R%d=%d) — модель кадр %d, Вектор %d (сдвиг %+d)'
                          % (nm, i, pr, pv, exp, gtf, d))
                mism_t += 1
        print('  %-26s записей %4d, расхождений значений %d, расхождений кадра >1: %d (макс |сдвиг| %d)'
              % (nm, n, mism_v, mism_t, worst))
        bad += mism_v + mism_t
    print('ИТОГ: %s' % ('ПОЛНОЕ СОВПАДЕНИЕ' if bad == 0 else 'РАСХОЖДЕНИЙ %d' % bad))
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
