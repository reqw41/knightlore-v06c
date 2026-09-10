#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mktitle.py — заставка Knight Lore из PNG в планарные образы Вектора-06Ц.

Железо:
  экран 256x256, 4 плоскости по 8К: 0x8000 p0, 0xA000 p1, 0xC000 p2, 0xE000 p3
  адрес байта = база + (X/8)*256 + Y, Y=0 — НИЖНЯЯ строка, старший бит байта — левый пиксель
  индекс палитры = p0<<3 | p1<<2 | p2<<1 | p3, цвет = байт %BBGGGRRR

Что свободно в момент загрузки: p0 и p1 целиком, p2 только столбцы 0..19 (0xC000..0xD3FF).
p2 столбцы 20..31 (0xD400..0xDFFF) и p3 целиком (0xE000..0xFFFF) заняты кодом игры,
поэтому палитра обязана быть нечувствительна к этим битам там, где мы их не контролируем.

Варианты раскладки (--mode):
  a4   4 цвета на весь экран: цвет зависит только от (p0,p1), биты p2/p3 не влияют.
  b53  3 цвета «везде» + 2 добавочных только в столбцах 0..19 (цвет по (p0,p1,p2), p3 не влияет);
       в столбцах 20..31 разрешены только те (p0,p1), у которых оба значения p2 дают один цвет.
  c8   8 цветов на весь экран — ТОЛЬКО если плоскость 2 освободится целиком (проверочный/«а если бы»).
  c16  16 цветов, все четыре плоскости заняты картинкой. Внутри игры так нельзя (p2/p3 — код),
       это отдельная демонстрация: образ title16.r0m грузит планы и палитру сам.

Пример:
  python3 tools/mktitle.py knightlore_src.png --out out/ --mode a4 --dither fs
"""

import argparse
import os
import sys

import numpy as np
from PIL import Image, ImageFilter

# ----------------------------------------------------------------------------
# палитра Вектора-06Ц: байт %BBGGGRRR
# ----------------------------------------------------------------------------

def vec_byte_to_rgb(v):
    r = (v >> 0) & 7
    g = (v >> 3) & 7
    b = (v >> 6) & 3
    return (r * 255 // 7, g * 255 // 7, b * 255 // 3)


VEC_RGB = np.array([vec_byte_to_rgb(v) for v in range(256)], dtype=np.float64)

GAMMA = 2.2


def to_lin(x):
    """sRGB 0..255 -> линейный свет 0..1 (приближение степенью)."""
    return np.power(np.clip(np.asarray(x, dtype=np.float64) / 255.0, 0, 1), GAMMA)


def to_srgb(x):
    return np.clip(np.power(np.clip(np.asarray(x, dtype=np.float64), 0, 1), 1.0 / GAMMA) * 255.0, 0, 255)


VEC_LIN = to_lin(VEC_RGB)                    # (256,3) в линейном свете
# веса каналов при поиске ближайшего цвета (яркостная метрика, R:G:B ~ 2:4:3)
W = np.array([2.0, 4.0, 3.0]) / 9.0


def dist2(a, b):
    """a: (...,3), b: (K,3) -> (...,K) взвешенный квадрат расстояния."""
    d = a[..., None, :] - b[None, ...] if a.ndim == 2 else a[..., None, :] - b
    return np.einsum('...c,c->...', d * d, W)


def snap(colors_lin):
    """Притянуть произвольные цвета к сетке 3-3-2 Вектора. Возвращает индексы байт."""
    d = dist2(colors_lin.reshape(-1, 3), VEC_LIN)
    return np.argmin(d, axis=1)


# ----------------------------------------------------------------------------
# ресемплинг исходника в кадр 256x256
# ----------------------------------------------------------------------------

def build_frame(src_path, scale, filt, rows, ox=None, oy=None):
    """Кадр 256 x rows (сверху вниз), картинка вписана с масштабом scale.

    scale=1.0 — исходник растянут на весь экран 256x256; это геометрически верно:
    C64-картинка 320x200 показывается на ТВ как 4:3, экран Вектора 256x256 — тоже 4:3.
    """
    im = Image.open(src_path).convert('RGB')
    w = int(round(256 * scale))
    h = int(round(256 * scale))
    if filt == 'box2':
        # двухшаговый: сперва в родные 320x200 площадным усреднением, потом в цель
        im = im.resize((320, 200), Image.BOX).resize((w, h), Image.LANCZOS)
    else:
        im = im.resize((w, h), {'box': Image.BOX, 'lanczos': Image.LANCZOS,
                                'bilinear': Image.BILINEAR, 'nearest': Image.NEAREST}[filt])
    canvas = Image.new('RGB', (256, rows), (0, 0, 0))
    x0 = (256 - w) // 2 if ox is None else ox
    y0 = (rows - h) // 2 if oy is None else oy
    canvas.paste(im, (x0, y0))
    return np.asarray(canvas, dtype=np.float64), (x0, y0, w, h)


# ----------------------------------------------------------------------------
# подбор палитры: k-means с привязкой к сетке Вектора и региональными ограничениями
# ----------------------------------------------------------------------------

VEC_G = VEC_RGB / 255.0                      # сетка Вектора в перцептивном (гамма) пространстве


def snap_g(colors_g):
    d = dist2(np.asarray(colors_g).reshape(-1, 3), VEC_G)
    return np.argmin(d, axis=1)


def fit_palette(frame, n, region_limit=None, pins=(), iters=30, seed=1):
    """Подобрать n цветов Вектора под кадр.

    Кластеризация идёт в гамма-пространстве (там евклидова метрика ближе к
    восприятию), а дизеринг потом — в линейном свете. Фит в линейном свете
    пробовали: средние уезжают к светлому, чёрный фон превращается в оливковый.

    frame: (H,W,3) sRGB 0..255
    region_limit: (mask, k) — пиксели mask пользуются только первыми k цветами
    pins: байты Вектора, которые обязаны быть в палитре (занимают первые места)
    Возвращает индексы байт палитры длиной n.
    """
    rng = np.random.default_rng(seed)
    px = frame.reshape(-1, 3) / 255.0
    npin = len(pins)
    if region_limit is not None:
        rmask = region_limit[0].reshape(-1)
        k = region_limit[1]
    else:
        rmask, k = None, n

    cent = np.zeros((n, 3))
    for i, b in enumerate(pins):
        cent[i] = VEC_G[b]
    # k-means++ для свободных мест
    have = list(cent[:npin]) if npin else [px[rng.integers(len(px))]]
    start = npin if npin else 1
    if not npin:
        cent[0] = have[0]
    for i in range(start, n):
        d = np.min(dist2(px, np.array(have)), axis=1)
        p = d / max(d.sum(), 1e-12)
        cent[i] = px[rng.choice(len(px), p=p)]
        have.append(cent[i])

    for it in range(iters):
        d = dist2(px, cent)
        if rmask is not None:
            d2 = d.copy()
            d2[rmask, k:] = np.inf          # правый регион — только первые k
            lab = np.argmin(d2, axis=1)
        else:
            lab = np.argmin(d, axis=1)
        new = cent.copy()
        for c in range(npin, n):
            m = lab == c
            if m.any():
                new[c] = px[m].mean(axis=0)
            else:                            # пустой кластер — перекинуть в худший пиксель
                new[c] = px[np.argmax(np.min(d, axis=1))]
        if it >= iters - 8:                  # хвост итераций — с привязкой к сетке 3-3-2
            new[npin:] = VEC_G[snap_g(new[npin:])]
        if np.allclose(new, cent):
            cent = new
            break
        cent = new
    return snap_g(cent)


def search_pair(frame, pins, n, step=2):
    """Полный перебор недостающих цветов при закреплённых pins (для n-len(pins)<=2).

    Метрика — ошибка при выборе ближайшего цвета (без дизеринга): именно так
    считается плоскоцветный C64-арт, ради которого палитра и подбирается.
    Перебор идёт по всем 256 цветам Вектора на прореженном кадре.
    """
    px = (frame[::step, ::step].reshape(-1, 3) / 255.0).astype(np.float32)
    D = np.einsum('nkc,c->nk', (px[:, None, :] - VEC_G[None, :, :].astype(np.float32)) ** 2,
                  W.astype(np.float32))                      # (N,256)
    base = np.min(D[:, list(pins)], axis=1) if pins else np.full(len(px), np.inf, np.float32)
    free = n - len(pins)
    if free == 1:
        sc = np.minimum(base[:, None], D).mean(axis=0)
        return tuple(pins) + (int(np.argmin(sc)),)
    best, bij = np.inf, None
    for i in range(256):
        b1 = np.minimum(base, D[:, i])
        sc = np.minimum(b1[:, None], D[:, i + 1:]).mean(axis=0)
        if len(sc):
            j = int(np.argmin(sc))
            if sc[j] < best:
                best, bij = sc[j], (i, i + 1 + j)
    return tuple(pins) + bij


# ----------------------------------------------------------------------------
# дизеринг
# ----------------------------------------------------------------------------

BAYER8 = np.array([
    [0, 32, 8, 40, 2, 34, 10, 42],
    [48, 16, 56, 24, 50, 18, 58, 26],
    [12, 44, 4, 36, 14, 46, 6, 38],
    [60, 28, 52, 20, 62, 30, 54, 22],
    [3, 35, 11, 43, 1, 33, 9, 41],
    [51, 19, 59, 27, 49, 17, 57, 25],
    [15, 47, 7, 39, 13, 45, 5, 37],
    [63, 31, 55, 23, 61, 29, 53, 21],
], dtype=np.float64)


def quant_none(img_lin, pal, allowed):
    """Ближайший цвет без дизеринга."""
    H, Wd = img_lin.shape[:2]
    out = np.zeros((H, Wd), dtype=np.int16)
    for a in np.unique(allowed):
        m = allowed == a
        sub = np.flatnonzero(np.array(ALLOW_SETS[a]))
        d = dist2(img_lin[m], pal[sub])
        out[m] = sub[np.argmin(d, axis=1)]
    return out


def quant_fs(img_lin, pal, allowed, strength=1.0):
    """Флойд–Стайнберг в линейном свете (правильное усреднение на экране).

    strength<1 гасит разносимую ошибку: исходник — плоскоцветный C64-арт,
    полная сила дизеринга съедает белую обводку логотипа и надпись ULTIMATE.
    """
    H, Wd = img_lin.shape[:2]
    buf = img_lin.copy()
    out = np.zeros((H, Wd), dtype=np.int16)
    subs = {a: np.flatnonzero(np.array(ALLOW_SETS[a])) for a in np.unique(allowed)}
    for y in range(H):
        serp = (y & 1) == 1
        xs = range(Wd - 1, -1, -1) if serp else range(Wd)
        for x in xs:
            old = buf[y, x]
            sub = subs[allowed[y, x]]
            i = sub[np.argmin(np.einsum('kc,c->k', (pal[sub] - old) ** 2, W))]
            out[y, x] = i
            err = (old - pal[i]) * strength
            if serp:
                if x > 0:
                    buf[y, x - 1] += err * (7 / 16)
                if y + 1 < H:
                    if x + 1 < Wd:
                        buf[y + 1, x + 1] += err * (3 / 16)
                    buf[y + 1, x] += err * (5 / 16)
                    if x > 0:
                        buf[y + 1, x - 1] += err * (1 / 16)
            else:
                if x + 1 < Wd:
                    buf[y, x + 1] += err * (7 / 16)
                if y + 1 < H:
                    if x > 0:
                        buf[y + 1, x - 1] += err * (3 / 16)
                    buf[y + 1, x] += err * (5 / 16)
                    if x + 1 < Wd:
                        buf[y + 1, x + 1] += err * (1 / 16)
    return out


def quant_ordered(img_lin, pal, allowed, steps=16):
    """Упорядоченный дизеринг с планом смешения двух цветов (по мотивам Yliluoma-1).

    Для каждого пикселя перебираются пары цветов палитры и доля t=k/steps;
    выбирается пара, чья линейная смесь ближе всего к пикселю. Дальше
    порог Байера решает, какой из двух цветов лечь в конкретный пиксель.
    """
    H, Wd = img_lin.shape[:2]
    out = np.zeros((H, Wd), dtype=np.int16)
    thr = (BAYER8[np.arange(H)[:, None] % 8, np.arange(Wd)[None, :] % 8] + 0.5) / 64.0
    for a in np.unique(allowed):
        sub = np.flatnonzero(np.array(ALLOW_SETS[a]))
        n = len(sub)
        pairs = [(i, j) for i in range(n) for j in range(n)]
        ts = np.arange(steps + 1) / steps
        # кандидаты: (n*n*(steps+1), 3)
        cand, meta = [], []
        for (i, j) in pairs:
            for t in ts:
                cand.append(pal[sub[i]] * (1 - t) + pal[sub[j]] * t)
                meta.append((sub[i], sub[j], t))
        cand = np.array(cand)
        meta_i = np.array([m[0] for m in meta])
        meta_j = np.array([m[1] for m in meta])
        meta_t = np.array([m[2] for m in meta])
        m = allowed == a
        px = img_lin[m]
        best = np.zeros(len(px), dtype=np.int64)
        CH = 8192
        for s in range(0, len(px), CH):
            best[s:s + CH] = np.argmin(dist2(px[s:s + CH], cand), axis=1)
        t = meta_t[best]
        pick = np.where(thr[m] < t, meta_j[best], meta_i[best])
        out[m] = pick
    return out


# ALLOW_SETS заполняется в main(): allowed[y,x] -> индекс набора разрешённых цветов
ALLOW_SETS = {}


# ----------------------------------------------------------------------------
# планарная упаковка
# ----------------------------------------------------------------------------

def encode_planes(ci, mode, rows):
    """ci: (rows,256) номера цветов. Возвращает (p0, p1, p2, p3) по 8К.

    Номер цвета c раскладывается по битам так, чтобы c совпал с индексом записи
    палитры p0<<3|p1<<2|p2<<1|p3 на своей глубине:
      a4      c -> p0=c>>1, p1=c&1                       (p2, p3 не используются)
      b53/c8  c -> p0=c>>2, p1=(c>>1)&1, p2=c&1          (p3 не используется)
      c16     c -> p0=c>>3, p1=(c>>2)&1, p2=(c>>1)&1, p3=c&1
    Адрес: (X>>3)*256 + Y, где Y = 255 - y_сверху; старший бит байта — левый пиксель.
    """
    p0 = np.zeros(8192, dtype=np.uint8)
    p1 = np.zeros(8192, dtype=np.uint8)
    p2 = np.zeros(8192, dtype=np.uint8)
    p3 = np.zeros(8192, dtype=np.uint8)
    for y in range(rows):
        Y = 255 - y
        row = ci[y]
        for x in range(256):
            c = int(row[x])
            if mode == 'a4':
                b0, b1, b2, b3 = (c >> 1) & 1, c & 1, 0, 0
            elif mode == 'c16':
                b0, b1, b2, b3 = (c >> 3) & 1, (c >> 2) & 1, (c >> 1) & 1, c & 1
            else:
                b0, b1, b2, b3 = (c >> 2) & 1, (c >> 1) & 1, c & 1, 0
            addr = (x >> 3) * 256 + Y
            bit = 1 << (7 - (x & 7))
            if b0:
                p0[addr] |= bit
            if b1:
                p1[addr] |= bit
            if b2:
                p2[addr] |= bit
            if b3:
                p3[addr] |= bit
    return p0, p1, p2, p3


def build_palette(mode, pal_srgb_bytes):
    """16 байт палитры Вектора. Индекс записи = p0<<3|p1<<2|p2<<1|p3."""
    out = np.zeros(16, dtype=np.uint8)
    for i in range(16):
        if mode == 'a4':
            c = i >> 2                      # (p0,p1); p2,p3 не влияют
        elif mode == 'c16':
            c = i                           # все четыре бита значимы
        else:
            c = i >> 1                      # (p0,p1,p2); p3 не влияет
        out[i] = pal_srgb_bytes[c]
    return out


# ----------------------------------------------------------------------------
# превью: симуляция экрана прямо из планарных байтов + палитры
# ----------------------------------------------------------------------------

def simulate(p0, p1, p2, pal16, rows, junk2=None, junk3=None, aspect=True, zoom=3,
             p2_len=8192, p3=None):
    """Собрать картинку из плоскостей ровно так, как её соберёт видеоконтроллер.

    junk2 — реальное содержимое 0xD400..0xDFFF (столбцы 20..31 плоскости 2),
    junk3 — 0xE000..0xFFFF (плоскость 3). Если заданы, превью показывает,
    действительно ли мусор кода не пролезает в изображение.
    """
    P2 = p2.copy()
    if junk2 is not None:
        # мусором забиваем ВСЁ, что не грузится из файла p2 (а не только столбцы
        # 20..31): так превью показывает ровно тот экран, что будет в машине
        P2[p2_len:] = junk2[p2_len:]
    P3 = (np.zeros(8192, dtype=np.uint8) if junk3 is None else junk3.copy()) if p3 is None else p3

    img = np.zeros((rows, 256, 3), dtype=np.uint8)
    for x in range(256):
        col = (x >> 3) * 256
        bit = 7 - (x & 7)
        for y in range(rows):
            Y = 255 - y
            idx = (((p0[col + Y] >> bit) & 1) << 3) | (((p1[col + Y] >> bit) & 1) << 2) \
                | (((P2[col + Y] >> bit) & 1) << 1) | ((P3[col + Y] >> bit) & 1)
            img[y, x] = vec_byte_to_rgb(int(pal16[idx]))
    im = Image.fromarray(img)
    im = im.resize((256 * zoom, rows * zoom), Image.NEAREST)
    if aspect:
        # экран 256x256 показывается как 4:3 => пиксель шире, чем выше, в 4/3 раза
        im = im.resize((256 * zoom, int(round(rows * zoom * 3 / 4))), Image.BOX)
    return im


# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('--out', default='.')
    ap.add_argument('--mode', choices=['a4', 'b53', 'c8', 'c16'], default='a4')
    ap.add_argument('--dither', choices=['none', 'fs', 'ordered'], default='fs')
    ap.add_argument('--filter', default='lanczos',
                    choices=['box', 'box2', 'lanczos', 'bilinear', 'nearest'])
    ap.add_argument('--scale', type=float, default=1.0,
                    help='1.0 — на весь экран; <1 — уменьшить (для b53 с целым логотипом)')
    ap.add_argument('--ox', type=int, default=None, help='левый край картинки в пикселях')
    ap.add_argument('--oy', type=int, default=None)
    ap.add_argument('--rows', type=int, default=256)
    ap.add_argument('--split', type=int, default=160,
                    help='граница регионов (столбец 20 = x 160)')
    ap.add_argument('--extra-x', type=int, default=None, dest='extra_x',
                    help='b53: добавочные цвета применять только левее этого X '
                         '(<= split). Логотип пересекает split, и если разрешить '
                         'ему добавочный цвет слева, на границе виден тональный шов; '
                         'волшебнику же добавочные цвета нужны, а он весь левее ~72.')
    ap.add_argument('--tag', default=None)
    ap.add_argument('--junk', default=None, help='.r0m игры — подмешать реальный код в p2/p3 в превью')
    ap.add_argument('--pins', default='0x00,0xFF',
                    help='цвета Вектора, закреплённые в палитре (чёрный фон и белый '
                         'обводки логотипа/луна занимают треть кадра каждый)')
    ap.add_argument('--strength', type=float, default=1.0,
                    help='сила Флойда–Стайнберга, 0..1 (0 = как none)')
    ap.add_argument('--palette', default=None,
                    help='явная палитра: байты Вектора через запятую вместо подбора')
    ap.add_argument('--tries', type=int, default=8,
                    help='c8/c16: сколько запусков k-means с разными зёрнами; берётся лучший')
    ap.add_argument('--search', action='store_true',
                    help='a4: полный перебор недостающих цветов вместо k-means')
    ap.add_argument('--b53fit', choices=['right-first', 'joint'], default='right-first')
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--no-preview', action='store_true')
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    tag = args.tag or f'{args.mode}_{args.dither}'

    frame, geom = build_frame(args.src, args.scale, args.filter, args.rows, args.ox, args.oy)
    img_lin = to_lin(frame)

    right = np.zeros((args.rows, 256), dtype=bool)
    right[:, args.split:] = True

    # --- палитра ------------------------------------------------------------
    global ALLOW_SETS
    pins = tuple(int(v, 0) for v in args.pins.split(',')) if args.pins else ()
    fixed = np.array([int(v, 0) for v in args.palette.split(',')]) if args.palette else None
    if args.mode == 'a4':
        if fixed is not None:
            pal_b = fixed
        elif args.search:
            pal_b = np.array(search_pair(frame, pins, 4))
        else:
            pal_b = fit_palette(frame, 4, pins=pins, seed=args.seed)
        pal = VEC_LIN[pal_b]
        # порядок цветов не важен: биты p2/p3 всё равно не влияют
        ALLOW_SETS = {0: [True] * 4}
        allowed = np.zeros((args.rows, 256), dtype=np.int8)
    elif args.mode in ('c8', 'c16'):
        n = 8 if args.mode == 'c8' else 16
        if fixed is not None:
            pal_b = fixed
        else:
            # k-means с одного зерна на 16 цветах заметно скачет — берём лучший
            # из нескольких запусков по ошибке ближайшего цвета
            px = frame.reshape(-1, 3) / 255.0
            best = None
            for k in range(args.tries):
                cand = fit_palette(frame, n, pins=pins, seed=args.seed + k)
                e = float(dist2(px, VEC_G[cand]).min(axis=1).mean())
                if best is None or e < best[0]:
                    best = (e, cand)
            pal_b = best[1]
        pal = VEC_LIN[pal_b]
        ALLOW_SETS = {0: [True] * n}
        allowed = np.zeros((args.rows, 256), dtype=np.int8)
    else:  # b53
        # 5 цветов; правый регион пользуется только первыми тремя
        if fixed is not None:
            pal_b = fixed
        elif args.b53fit == 'right-first':
            # 3 «общих» цвета обязаны обслуживать столбцы 20..31 (луна, оборотень,
            # звёзды) — там p2 чужой. Подбираем их ТОЛЬКО по правому региону,
            # иначе масса волшебника утягивает общий третий цвет в сиреневый,
            # и луна становится фиолетовой.
            rframe = frame[:, args.split:]
            com = fit_palette(rframe, 3, pins=pins, seed=args.seed)
            lframe = frame[:, :args.split]
            pal_b = fit_palette(lframe, 5, pins=tuple(int(v) for v in com), seed=args.seed)
        else:
            pal_b = fit_palette(frame, 5, region_limit=(right, 3), pins=pins, seed=args.seed)
        pal5 = VEC_LIN[pal_b]
        # раскладка по 8 записям (p0,p1,p2):
        #   c=0,1 -> K0   c=2,3 -> K1   c=4,5 -> K2   c=6 -> L0   c=7 -> L1
        pal = np.zeros((8, 3))
        pal[0] = pal[1] = pal5[0]
        pal[2] = pal[3] = pal5[1]
        pal[4] = pal[5] = pal5[2]
        pal[6] = pal5[3]
        pal[7] = pal5[4]
        # в правом регионе разрешены только c=0,2,4 (там p2 — чужой байт кода)
        ALLOW_SETS = {0: [True, False, True, False, True, False, True, True],
                      1: [True, False, True, False, True, False, False, False]}
        ex = args.split if args.extra_x is None else min(args.extra_x, args.split)
        allowed = np.ones((args.rows, 256), dtype=np.int8)
        allowed[:, :ex] = 0                  # 0 — набор с добавочными цветами

    # --- квантование --------------------------------------------------------
    if args.dither == 'fs':
        ci = quant_fs(img_lin, pal, allowed, args.strength)
    elif args.dither == 'ordered':
        ci = quant_ordered(img_lin, pal, allowed)
    else:
        ci = quant_none(img_lin, pal, allowed)

    # --- ошибка -------------------------------------------------------------
    # «сырой» RMSE штрафует сам дизеринг; честнее сравнивать после лёгкого
    # размытия — так усредняет соседние точки глаз (и люминофор).
    out_srgb = to_srgb(pal[ci])
    err = np.sqrt(np.mean((out_srgb - frame) ** 2))
    blur = lambda a: np.asarray(Image.fromarray(np.uint8(np.clip(a, 0, 255)))
                                .filter(ImageFilter.GaussianBlur(1.0)), dtype=np.float64)
    errb = np.sqrt(np.mean((blur(out_srgb) - blur(frame)) ** 2))
    print(f'[{tag}] кадр {geom[2]}x{geom[3]} @ ({geom[0]},{geom[1]}), '
          f'RMSE = {err:.2f}, RMSE после размытия = {errb:.2f}')

    pal_bytes = snap(pal.reshape(-1, 3))
    used = sorted(set(int(v) for v in np.unique(ci)))
    print('  палитра:', ', '.join(
        f'c{c}=0x{pal_bytes[c]:02X}{vec_byte_to_rgb(int(pal_bytes[c]))}' for c in used))

    # --- планарные файлы ----------------------------------------------------
    p0, p1, p2, p3 = encode_planes(ci, args.mode, args.rows)
    pal16 = build_palette(args.mode, pal_bytes)
    p2_len = 8192 if args.mode in ('c8', 'c16') else 0

    if args.mode == 'c16':
        base = os.path.join(args.out, 'title16' + (f'_{args.tag}' if args.tag else ''))
    else:
        base = os.path.join(args.out, f'title_{tag}')
    open(base + '_p0.bin', 'wb').write(p0.tobytes())
    open(base + '_p1.bin', 'wb').write(p1.tobytes())
    if args.mode == 'b53':
        # p2 нужен только там, где реально стоят добавочные цвета: в остальных
        # столбцах используются лишь p2-нечувствительные (p0,p1), и что лежит
        # в этих байтах — неважно. Грузить и стирать надо ровно этот кусок.
        nz = np.flatnonzero(p2[:20 * 256])
        last = (int(nz[-1]) // 256 + 1) if len(nz) else 0
        open(base + '_p2.bin', 'wb').write(p2[:last * 256].tobytes())
        p2_len = last * 256
        print(f'  p2: столбцы 0..{last - 1} -> 0xC000..0x{0xC000 + last * 256 - 1:04X} '
              f'({last * 256} байт); остальные столбцы плоскости 2 на картинку не влияют')
    elif args.mode in ('c8', 'c16'):
        open(base + '_p2.bin', 'wb').write(p2.tobytes())
        if args.mode == 'c16':
            open(base + '_p3.bin', 'wb').write(p3.tobytes())
    open(base + '.pal', 'wb').write(pal16.tobytes())
    print('  палитра 16 байт:', ' '.join(f'{b:02X}' for b in pal16))

    # --- превью -------------------------------------------------------------
    if not args.no_preview:
        junk2 = junk3 = None
        if args.junk and os.path.exists(args.junk):
            rom = np.frombuffer(open(args.junk, 'rb').read(), dtype=np.uint8)
            if len(rom) >= 0x10000:
                junk2 = rom[0xC000:0xE000].copy()
                junk3 = rom[0xE000:0x10000].copy()
        if args.mode in ('c8', 'c16'):
            junk2 = None                    # плоскость 2 занята картинкой, не кодом
        im = simulate(p0, p1, p2, pal16, args.rows, junk2, junk3, p2_len=p2_len,
                      p3=(p3 if args.mode == 'c16' else None))
        im.save(base + '_preview.png')
        print('  превью:', base + '_preview.png')


if __name__ == '__main__':
    main()
