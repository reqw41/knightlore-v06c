#!/usr/bin/env python3
"""Спецификации процедур Knight Lore на Python + тест против дампов живых вызовов (build/dump_<label>.jsonl).
Модель получает состояние на входе (регистры, регионы памяти) и обязана дать выход байт в байт.
Запуск: python3 spec/kl_spec.py [label ...]   (по умолчанию все известные)
"""
import json, sys, os

# --- поля объекта (32 байта) ---
F_GFX, F_X, F_Y, F_Z, F_W, F_D, F_H, F_FLAGS = 0, 1, 2, 3, 4, 5, 6, 7
F_ADJX, F_ADJY = 18, 19
F_PW, F_PH, F_PX, F_PY = 24, 25, 26, 27          # ширина(байт), высота, пиксель X, пиксель Y
F_OPW, F_OPH, F_OPX, F_OPY = 28, 29, 30, 31      # то же «старое»

def u8(x): return x & 0xFF

def spec_save_2d_info(obj):
    """obj[28..31] = obj[24..27]. Возвращает новый obj. Регистры: A = obj[27] (последняя пересылка)."""
    o = bytearray(obj)
    o[F_OPW], o[F_OPH], o[F_OPX], o[F_OPY] = o[F_PW], o[F_PH], o[F_PX], o[F_PY]
    return o

def spec_calc_pixel_XY(obj):
    """obj[26] = X+Y-128+adjX; obj[27] = ((Y-X+128)>>1) + Z - 104 + adjY (всё по модулю 256).
    Возвращает (obj, carry): carry = pixelY < 192 (флаг C после cp 192; вызывающий ret NC = за экраном)."""
    o = bytearray(obj)
    X, Y, Z = o[F_X], o[F_Y], o[F_Z]
    o[F_PX] = u8(u8(u8(X + Y) - 128) + o[F_ADJX])
    t = u8(u8(Y - X) + 128) >> 1
    py = u8(u8(u8(t + Z) - 104) + o[F_ADJY])
    o[F_PY] = py
    return o, py < 192

def spec_list_objects_to_draw(objtbl, lst):
    """Индексы объектов с graphic_no != 0 и флагом DRAW (бит 4 поля 7), в порядке таблицы, терминатор 0xFF."""
    out = bytearray(lst)
    k = 0
    for i in range(40):
        o = objtbl[i*32:(i+1)*32]
        if o[F_GFX] != 0 and (o[F_FLAGS] & 0x10):
            out[k] = i; k += 1
    out[k] = 0xFF
    return out

REV = bytes(int('{:08b}'.format(i)[::-1], 2) for i in range(256))

def spec_flip_sprite_data(obj, spr):
    """Спрайт: [0]=флаги(бит7 V, бит6 H)|ширина(биты0-3), [1]=высота, далее строки по 2*w байт (маска,данные).
    Приводит хранимую ориентацию к требуемой obj[7] (биты 6,7). V-флип — обмен строк; H-флип — зеркало пар
    в строке с бит-реверсом каждого байта (порядок маска/данные в паре сохраняется)."""
    s = bytearray(spr)
    a = s[0] ^ obj[F_FLAGS]
    if (a & 0xC0) == 0:
        return s
    w = s[0] & 0x0F; h = s[1]; w2 = 2 * w
    if a & 0x80:                                     # V-флип
        s[0] ^= 0x80
        for i in range(h // 2):
            j = h - 1 - i
            for k in range(w2):
                s[2 + i*w2 + k], s[2 + j*w2 + k] = s[2 + j*w2 + k], s[2 + i*w2 + k]
        a = s[0] ^ obj[F_FLAGS]
    if (a & 0x40) == 0:
        return s
    s[0] ^= 0x40                                     # H-флип
    for i in range(h):
        line = s[2 + i*w2: 2 + (i+1)*w2]
        pairs = [(REV[line[2*k]], REV[line[2*k+1]]) for k in range(w)]
        pairs.reverse()
        flat = bytearray()
        for m, d in pairs: flat += bytes([m, d])
        s[2 + i*w2: 2 + (i+1)*w2] = flat
    return s

def spec_overlap_class(o1, o2):
    """Класс перекрытия 0..26 (C в loc_CF62): по осям Z (0/1/2), Y (0/3/6), X (0/9/18) — сумма.
    Каждая ось: если A1 >= B2+S2 (8-бит) -> 0; иначе если B2 < A1+S1 (8-бит) -> +1 (перекрытие) иначе +2."""
    c = 0
    # Z: без вычитания глубины
    l = u8(o2[F_Z] + o2[F_H])
    if o1[F_Z] < l:                                   # borrow после sub l
        l = u8(o1[F_Z] + o1[F_H])
        c += 1 if o2[F_Z] < l else 2
    # Y: Y1-D1-(Y2+D2): два вычитания, заём от последнего
    l = u8(o2[F_Y] + o2[F_D])
    if u8(o1[F_Y] - o1[F_D]) < l:
        l = u8(o1[F_Y] + o1[F_D])
        c += 3 if u8(o2[F_Y] - o2[F_D]) < l else 6
    # X
    l = u8(o2[F_X] + o2[F_W])
    if u8(o1[F_X] - o1[F_W]) < l:
        l = u8(o1[F_X] + o1[F_W])
        c += 9 if u8(o2[F_X] - o2[F_W]) < l else 18
    return c

def spec_calc_2d_info(obj, spr):
    """calc_pixel_XY; flip_sprite (обмен ориентации спрайта под obj[7]); obj[24] = (ширина + (1 если pixelX&7)) & 0xF;
    obj[25] = высота. Возвращает (obj, spr, carry как у calc_pixel_XY? нет — флаги здесь не читаются)."""
    o, _ = spec_calc_pixel_XY(obj)
    s = spec_flip_sprite_data(o, spr)
    wb = s[0]
    if o[F_PX] & 7: wb = u8(wb + 1)
    o[F_PW] = wb & 0x0F
    o[F_PH] = s[1]
    return o, s

def spec_set_draw_objs_overlapped(obj, objtbl, spr):
    """calc_2d_info на obj, затем для всех 40 объектов таблицы: если объект не пуст и без флага DRAW и его
    (pixelX/8, pixelY) попадает в объединённый прямоугольник старого и нового положения obj (с учётом
    собственной ширины/высоты объекта при отрицательном смещении) — поставить DRAW (бит 4 поля 7).
    Возвращает (obj, objtbl, spr)."""
    o, s = spec_calc_2d_info(obj, spr)
    xb = (o[F_PX] >> 3) & 0x1F; oxb = (o[F_OPX] >> 3) & 0x1F
    e = xb if oxb >= xb else oxb                     # левый край (байт)
    l1 = u8(xb + o[F_PW]); a = u8(oxb + o[F_OPW])
    if a < l1: a = l1
    d = u8(a - e)                                    # объединённая ширина
    y = o[F_PY]; oy = o[F_OPY]
    lo = y if y < oy else oy                          # cp (ix+31): jr C -> a=pixelY остаётся; иначе old
    hh = u8(y + o[F_PH]); a = u8(oy + o[F_OPH])
    if a < hh: a = hh
    h = u8(a - lo)                                   # объединённая высота
    t = bytearray(objtbl)
    for i in range(40):
        q = t[i*32:(i+1)*32]
        if q[F_GFX] == 0 or (q[F_FLAGS] & 0x10): continue
        ax = (q[F_PX] >> 3) & 0x1F
        a = u8(ax - e)
        if ax < e:
            a = u8(e - ax)
            if a >= q[F_PW]: continue
        elif a >= d: continue
        ay = q[F_PY]
        a = u8(ay - lo)
        if ay < lo:
            a = u8(lo - ay)
            if a >= q[F_PH]: continue
        elif a >= h: continue
        t[i*32 + F_FLAGS] |= 0x10
    # сам obj в таблице: объект obj — один из 40, его копия в objtbl должна отражать calc_2d_info
    return o, t, s

# ---------------- тест против дампов ----------------
def load(label):
    p = f"build/dump_{label}.jsonl"
    if not os.path.exists(p): return None
    return [json.loads(l) for l in open(p) if l.strip()]

def mem(rec, side, name):
    for n, a, h in rec[side]['mem']:
        if n == name: return bytes.fromhex(h)
    raise KeyError(name)

def check(label):
    recs = load(label)
    if recs is None: print(f"{label}: нет дампа"); return
    bad = 0; first = None
    for r in recs:
        ok = True
        if label == 'save_2d_info':
            ok = spec_save_2d_info(mem(r,'entry','obj')) == mem(r,'exit','obj')
        elif label == 'calc_pixel_XY':
            o, cy = spec_calc_pixel_XY(mem(r,'entry','obj'))
            ok = o == mem(r,'exit','obj') and cy == bool(r['exit']['regs']['f'] & 1)
        elif label == 'list_objects_to_draw':
            ok = spec_list_objects_to_draw(mem(r,'entry','objtbl'), mem(r,'entry','list')) == mem(r,'exit','list') and mem(r,'entry','objtbl') == mem(r,'exit','objtbl')
        elif label == 'flip_sprite_data':
            ok = spec_flip_sprite_data(mem(r,'entry','obj'), mem(r,'entry','spr')) == mem(r,'exit','spr')
        elif label == 'calc_2d_info':
            o, sp = spec_calc_2d_info(mem(r,'entry','obj'), mem(r,'entry','spr'))
            ok = o == mem(r,'exit','obj') and sp == mem(r,'exit','spr')
        elif label == 'set_draw_objs_overlapped':
            o, t, sp = spec_set_draw_objs_overlapped(mem(r,'entry','obj'), mem(r,'entry','objtbl'), mem(r,'entry','spr'))
            # obj лежит внутри objtbl: наложить o на копию таблицы
            ix = r['entry']['regs']['ix']; base = None
            for n,a,h in r['entry']['mem']:
                if n == 'objtbl': base = a
            off = ix - base
            if 0 <= off < 40*32: t[off:off+32] = o
            ok = t == mem(r,'exit','objtbl') and sp == mem(r,'exit','spr') and o == mem(r,'exit','obj')
        elif label == 'loc_CF62':
            ok = spec_overlap_class(mem(r,'entry','obj1'), mem(r,'entry','obj2')) == r['entry']['regs']['c']
        if not ok:
            bad += 1
            if first is None: first = r
    print(f"{label}: вызовов {len(recs)}, расхождений {bad}")
    if first is not None:
        e = first['entry']; print("  первое расхождение: iter", first['iter'], "regs", {k: hex(v) for k, v in e['regs'].items()})
        for n, a, h in e['mem']: print("   вход %s @%04X: %s" % (n, a, h[:96]))
        if 'exit' in first:
            for n, a, h in first['exit']['mem']: print("   выход %s @%04X: %s" % (n, a, h[:96]))

if __name__ == '__main__':
    labels = sys.argv[1:] or ['save_2d_info', 'calc_pixel_XY', 'list_objects_to_draw', 'flip_sprite_data', 'loc_CF62', 'calc_2d_info', 'set_draw_objs_overlapped']
    for l in labels: check(l)
