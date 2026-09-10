#!/usr/bin/env python3
"""Модель правила перерисовки по дампу render_dynamic_objects (tools/calldump_vec.js).

Снимок снимается ПЕРЕД стиранием и рисованием, поэтому в нём видно всё, что нужно:
  * флаг WIPE (бит 5 поля +7) -> объект будет стёрт, стирается объединённый прямоугольник
    старого (+28..+31) и нового (+24..+27) положения — ровно тот же, что считает
    set_draw_objs_overlapped;
  * флаг DRAW (бит 4 поля +7) -> объект попал в objects_to_draw и будет перерисован.

Считаем три множества:
  actual  — что помечено DRAW в живой сборке (текущее правило);
  exact   — «новый 2D-прямоугольник пересекает прямоугольник стирания хоть одного стёртого
            объекта» + сами стёртые (минимально достаточное множество без учёта масок);
  closure — exact + транзитивное замыкание: если перерисовывается объект A, то всё, что
            рисуется ПОСЛЕ него (ближе к зрителю) и пересекается с ним по прямоугольнику,
            тоже должно быть перерисовано.

  python3 tools/kl_redraw_model.py build/dump_render_dynamic_objects.jsonl
"""
import json, sys
from collections import Counter

DRAW, WIPE = 0x10, 0x20


def objs(hexstr):
    b = bytes.fromhex(hexstr)
    return [b[i * 32:(i + 1) * 32] for i in range(40)]


def rect_new(o):
    """Прямоугольник нового положения: байтовый X, ширина в байтах, пиксельный Y, высота."""
    x0 = (o[26] >> 3) & 0x1F
    return (x0, x0 + o[24], o[27], o[27] + o[25])


def rect_wipe(o):
    """Прямоугольник стирания = объединение старого и нового (как в wipe_next_object)."""
    xn, xo = (o[26] >> 3) & 0x1F, (o[30] >> 3) & 0x1F
    x0 = min(xn, xo)
    x1 = max(xn + o[24], xo + o[28])
    y0 = min(o[27], o[31])
    y1 = max(o[27] + o[25], o[31] + o[29])
    return (x0, x1, y0, y1)


def rect_old(o):
    """Прямоугольник СТАРОГО положения (то, что реально надо стереть)."""
    x0 = (o[30] >> 3) & 0x1F
    return (x0, x0 + o[28], o[31], o[31] + o[29])


def hit(a, b):
    return a[0] < b[1] and b[0] < a[1] and a[2] < b[3] and b[2] < a[3]


def depth_key(o):
    """Порядок рисования: чем дальше объект, тем раньше. Приближение по X+Y+Z."""
    return (o[1] + o[2] + o[3])


def analyse(path):
    stat = Counter()
    iters = 0
    per_iter = []
    for line in open(path):
        rec = json.loads(line)
        mem = dict((n, h) for n, a, h in rec["entry"]["mem"])
        t = objs(mem["objtbl"])
        live = [i for i in range(40) if t[i][0]]
        wiped = [i for i in live if t[i][7] & WIPE]
        actual = set(i for i in live if t[i][7] & DRAW)
        wr = [rect_wipe(t[i]) for i in wiped]
        exact = set(wiped)
        for i in live:
            r = rect_new(t[i])
            if any(hit(r, w) for w in wr):
                exact.add(i)
        # транзитивное замыкание «вперёд по глубине»
        order = sorted(live, key=lambda i: depth_key(t[i]))
        pos = {o: k for k, o in enumerate(order)}
        closure = set(exact)
        changed = True
        while changed:
            changed = False
            for a in list(closure):
                ra = rect_new(t[a])
                for b in live:
                    if b in closure or pos[b] <= pos[a]:
                        continue
                    if hit(ra, rect_new(t[b])):
                        closure.add(b)
                        changed = True
        # НИЖНЯЯ граница любого правила, сохраняющего рисование спрайтов как есть:
        # стирать надо только СТАРОЕ положение (новое перекрывается маской спрайта),
        # значит перерисовать обязаны только те, кого задел старый прямоугольник.
        # Требования порядка (перекрывающие спереди в НОВОМ месте) сюда не включены — они только добавляют.
        orold = [rect_old(t[i]) for i in wiped]
        lower = set(wiped)
        for i in live:
            r = rect_new(t[i])
            if any(hit(r, w) for w in orold):
                lower.add(i)
        stat["lower"] += len(lower)
        iters += 1
        stat["live"] += len(live)
        stat["wiped"] += len(wiped)
        stat["actual"] += len(actual)
        stat["exact"] += len(exact)
        stat["closure"] += len(closure)
        stat["exact_extra"] += len(exact - actual)
        stat["actual_extra"] += len(actual - exact)
        per_iter.append((rec["iter"], len(actual), len(exact), len(closure)))
    n = max(iters, 1)
    print(f"{path}: итераций {iters}")
    print(f"  живых объектов/итер   {stat['live']/n:6.2f}")
    print(f"  стёртых (WIPE)/итер   {stat['wiped']/n:6.2f}")
    print(f"  DRAW сейчас/итер      {stat['actual']/n:6.2f}")
    print(f"  точное правило/итер   {stat['exact']/n:6.2f}   (лишних у текущего {stat['actual_extra']/n:.2f}, недостающих {stat['exact_extra']/n:.2f})")
    print(f"  + замыкание/итер      {stat['closure']/n:6.2f}")
    print(f"  НИЖНЯЯ граница/итер   {stat['lower']/n:6.2f}   (стирать только старое положение; выигрыш {100*(stat['actual']-stat['lower'])/max(stat['actual'],1):.1f} %)")
    return per_iter


if __name__ == "__main__":
    for p in sys.argv[1:]:
        analyse(p)
