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


# --- кадрирование живописного исходника (второй заход, §8 TITLE.md) ---------
#
# Пиксель Вектора шире, чем выше, в 4/3 раза (256x256 = весь кадр 4:3). Значит
# кусок исходника шириной W и высотой H точек выглядит на экране НЕискажённым,
# если занять им w экранных столбцов и h строк так, что (w*4/3)/h = W/H.
# Отсюда два режима:
#   stretch — растянуть кусок на все 256x256 (годится, когда сам кусок 4:3);
#   height  — вписать по высоте: h = rows, w = round(rows * (W/H) * 3/4);
#   width   — вписать по ширине: w = 256, h = round(256 * (H/W) * 4/3).
# Поля заливаются --bg. Горизонталь и вертикаль масштабируются РАЗНЫМИ
# коэффициентами — это и есть поправка на неквадратный пиксель, а не искажение.

def build_frame_crop(src_path, crop, fit, filt, rows, bg=(0, 0, 0), ox=None, oy=None):
    im = Image.open(src_path).convert('RGB')
    if crop:
        im = im.crop(crop)
    W, H = im.size
    if fit == 'stretch':
        w, h = 256, rows
    elif fit == 'height':
        h = rows
        w = min(256, int(round(rows * (W / H) * 3.0 / 4.0)))
    else:                                    # width
        w = 256
        h = min(rows, int(round(256 * (H / W) * 4.0 / 3.0)))
    rf = {'box': Image.BOX, 'lanczos': Image.LANCZOS, 'bilinear': Image.BILINEAR,
          'nearest': Image.NEAREST}.get(filt, Image.LANCZOS)
    im = im.resize((w, h), rf)
    canvas = Image.new('RGB', (256, rows), tuple(bg))
    x0 = (256 - w) // 2 if ox is None else ox
    y0 = (rows - h) // 2 if oy is None else oy
    canvas.paste(im, (x0, y0))
    return np.asarray(canvas, dtype=np.float64), (x0, y0, w, h)


# --- ZX-заставка: 256x192 родных точек, 15 цветов --------------------------
#
# Горизонталь ZX и Вектора совпадает точка в точку (256), пиксель ZX квадратный,
# и 192 строки ZX — это тот же кадр 4:3. Значит пересчёт ровно один: 192 -> 256
# по вертикали, то есть строго x4/3 (3 исходные строки на 4 экранных), а по
# горизонтали НИЧЕГО не делается. Единственный источник с родным разрешением.

ZX_PAL = [(0, 0, 0), (0, 0, 205), (205, 0, 0), (205, 0, 205),
          (0, 205, 0), (0, 205, 205), (205, 205, 0), (205, 205, 205),
          (0, 0, 0), (0, 0, 255), (255, 0, 0), (255, 0, 255),
          (0, 255, 0), (0, 255, 255), (255, 255, 0), (255, 255, 255)]


def read_scr(path):
    """.scr 6912 байт -> (256,192) номера цветов 0..15 (8 и 0 — оба чёрный)."""
    d = np.frombuffer(open(path, 'rb').read(), dtype=np.uint8)
    if len(d) < 6912:
        raise SystemExit('ожидался .scr 6912 байт')
    out = np.zeros((192, 256), dtype=np.int16)
    for y in range(192):
        addr = ((y & 0xC0) << 5) | ((y & 7) << 8) | ((y & 0x38) << 2)
        at = d[6144 + (y >> 3) * 32:6144 + (y >> 3) * 32 + 32]
        for xb in range(32):
            b = int(d[addr + xb])
            a = int(at[xb])
            br = (a & 0x40) >> 3
            ink, pap = (a & 7) | br, ((a >> 3) & 7) | br
            for i in range(8):
                out[y, xb * 8 + i] = ink if (b >> (7 - i)) & 1 else pap
    return out


# ----------------------------------------------------------------------------
# восстановление родной сетки C64 и пиксель-арт-пересчёт
# ----------------------------------------------------------------------------
# Исходник — апскейл C64-картинки с интерполяцией. Сетка найдена по краевой
# энергии и уточнена по прямому критерию «снять сетку -> развернуть обратно
# ближайшим -> сравнить с исходником»: 160 x 200 ячеек (multicolour: по
# горизонтали 5 исходных точек на ячейку), шаг 4.98 / 2.49, смещение 3.25 / 2.00.
# Контраст «энергия на границе / энергия в середине ячейки» по горизонтали 8.1 —
# сетка стоит твёрдо; по вертикали 1.4, потому что ячейка всего 2.5 точки и
# интерполяция её замывает, поэтому вертикаль и подбиралась по остатку.
GRID = dict(nx=160, ny=200, sx=4.98, ox=3.25, sy=2.49, oy=2.00)

# 15 цветов исходника (k-means по снятой сетке; «почти белый» слит с белым —
# 77 % его ячеек стоят вплотную к чистому белому, то есть это шов интерполяции).
# Порядок фиксирован: по нему адресуются альтернативные палитры.
NATIVE_COLORS = [
    ('чёрный',         (2, 2, 2)),        # 43.6 % — фон, силуэт оборотня
    ('белый',          (254, 254, 254)),  # 19.7 % — обводка логотипа, диск луны, ULTIMATE
    ('светло-серый',   (193, 193, 193)),  #  8.3 % — светлая шерсть, свет луны
    ('синий',          (64, 54, 218)),    #  4.8 % — мантия волшебника
    ('серый',          (141, 143, 141)),  #  4.5 % — средняя шерсть, тень луны
    ('оранжевый',      (214, 125, 39)),   #  4.1 % — тело букв логотипа
    ('пурпурный',      (213, 86, 206)),   #  3.2 % — блики мантии
    ('жёлтый',         (247, 238, 77)),   #  2.5 % — блик логотипа, звёзды
    ('красный',        (206, 47, 47)),    #  1.8 % — тёмное в логотипе
    ('коричневый',     (150, 93, 43)),    #  1.7 % — посох, тень логотипа
    ('розовый',        (252, 154, 150)),  #  1.5 % — лицо и кисти рук
    ('тёмно-серый',    (81, 81, 80)),     #  1.4 % — глубокая тень
    ('голубой',        (121, 195, 237)),  #  1.3 % — буквы ULTIMATE
    ('светло-зелёный', (152, 249, 150)),  #  0.8 % — PLAY THE GAME, светлое
    ('зелёный',        (86, 211, 52)),    #  0.7 % — PLAY THE GAME
]
NATIVE_RGB = np.array([c for _, c in NATIVE_COLORS], dtype=np.float64)


def vb(r, g, b):
    """Байт Вектора из компонент: r,g в 0..7, b в 0..3."""
    return (b << 6) | (g << 3) | r


# Альтернативные палитры: байт Вектора для каждого цвета исходника, в порядке
# NATIVE_COLORS. Планы от палитры не зависят — меняются ровно 16 байт.
NAMED_PALETTES = {
    # насыщенные чистые тона: золото в логотипе, сочная сине-фиолетовая мантия,
    # холодная серо-голубая луна, белые звёзды
    'gold': [vb(0,0,0), vb(7,7,3), vb(5,6,3), vb(1,1,3), vb(3,4,2), vb(7,4,0),
             vb(5,2,3), vb(7,7,0), vb(6,1,0), vb(4,2,0), vb(7,5,2), vb(2,2,1),
             vb(2,6,3), vb(4,7,1), vb(1,6,0)],
    # тёплая «пергаментная» с холодной луной: тепло уходит в фон, логотип и мантию,
    # а полутона луны и шерсти (светло-серый/серый — они почти только там и встречаются)
    # остаются холодными синими. Пробовал наоборот, сделать пергаментным сам белый —
    # луна выходит кремовой, и холодного пятна в кадре не остаётся.
    'parchment': [vb(1,1,0), vb(7,7,3), vb(4,5,3), vb(5,3,1), vb(2,3,2), vb(7,4,0),
                  vb(6,3,1), vb(7,7,1), vb(6,2,0), vb(4,3,0), vb(7,5,2), vb(2,1,0),
                  vb(3,5,3), vb(6,6,2), vb(4,4,1)],
    # ZX-подобная: только базовые яркие и «неяркие» тона спектрумовской восьмёрки
    'zx': [0x00, 0xFF, 0xAD, 0xC0, 0xA8, 0x07,
           0x85, 0x3F, 0x05, 0x2D, 0xC7, 0x80,
           0xF8, 0x38, 0x28],
}


def native_grid(src_path, tab=None):
    """Снять родную сетку C64 мажоритарно: каждый исходный пиксель относим к
    ближайшему цвету исходника, затем по середине ячейки берём большинство.

    Мажоритарно, а не медианой RGB: медиана по паре размытых точек даёт
    промежуточный цвет, которого в оригинале не было, и штрих «плывёт».
    """
    tab = NATIVE_RGB if tab is None else tab
    a = np.asarray(Image.open(src_path).convert('RGB'), dtype=np.float64)
    H, W = a.shape[:2]
    idx = np.argmin(((a.reshape(-1, 1, 3) - tab[None]) ** 2).sum(-1), axis=1)             .reshape(H, W).astype(np.int16)
    g = GRID
    out = np.zeros((g['ny'], g['nx']), dtype=np.uint8)
    m = 0.20                                   # поля ячейки, где сидит шов интерполяции
    for j in range(g['ny']):
        y0 = g['oy'] + j * g['sy']
        ya = min(max(int(np.floor(y0 + m * g['sy'] + 0.5)), 0), H - 1)
        yb = min(max(int(np.ceil(y0 + (1 - m) * g['sy'] - 0.5)) + 1, ya + 1), H)
        for i in range(g['nx']):
            x0 = g['ox'] + i * g['sx']
            xa = min(max(int(np.floor(x0 + m * g['sx'] + 0.5)), 0), W - 1)
            xb = min(max(int(np.ceil(x0 + (1 - m) * g['sx'] - 0.5)) + 1, xa + 1), W)
            out[j, i] = np.bincount(idx[ya:yb, xa:xb].ravel(),
                                    minlength=len(tab)).argmax()
    return out


def native_to_frame(nat, how, rows=256):
    """Родная сетка 160x200 -> поле 256 x rows индексов цвета исходника.

    Обе оси — увеличение (160->256 это x1.6, 200->256 это x1.28), поэтому
    содержимое не теряется вовсе: каждая точка экрана берёт ровно одну ячейку.

    Брать надо по ЦЕНТРУ точки, а не по её левому краю: точка покрывает 0.625
    ячейки, то есть меньше одной, и ячейка, накрывающая центр, — это ровно та,
    которой досталось больше половины площади. Поэтому «ближайший по центру» и
    «мажоритарный по площади» здесь тождественны (majority_resample это
    пересчитывает честно, а --resample majority сверяет одно с другим).
    """
    ny, nx = nat.shape
    out = np.zeros((rows, 256), dtype=np.uint8)
    xi = np.minimum(((np.arange(256) + 0.5) * nx / 256).astype(int), nx - 1)
    if how == 'letterbox':
        # 200 строк один к одному, чёрные поля 28+28; по горизонтали 160->256
        y0 = (rows - ny) // 2
        out[y0:y0 + ny] = nat[:, xi]
    else:
        yi = np.minimum(((np.arange(rows) + 0.5) * ny / rows).astype(int), ny - 1)
        out[:] = nat[yi][:, xi]
    return out


def majority_resample(nat, rows=256, ncol=16):
    """Честный мажоритарный пересчёт по площади: для каждой точки экрана
    считается, какая ячейка накрыла её большей частью. Служит проверкой того,
    что пересчёт по центру ей равен."""
    ny, nx = nat.shape
    def weights(n_src, n_dst):
        w = np.zeros((n_dst, n_src))
        for d in range(n_dst):
            a, b = d * n_src / n_dst, (d + 1) * n_src / n_dst
            for c in range(int(a), min(int(np.ceil(b)), n_src)):
                w[d, c] = max(0.0, min(b, c + 1) - max(a, c))
        return w
    wx, wy = weights(nx, 256), weights(ny, rows)
    out = np.zeros((rows, 256), dtype=np.uint8)
    for c in range(ncol):
        m = (nat == c).astype(float)
        acc = wy @ m @ wx.T
        if c == 0:
            best, arg = acc, np.zeros_like(out)
        else:
            upd = acc > best
            best = np.where(upd, acc, best)
            arg[upd] = c
    return arg


# ----------------------------------------------------------------------------
# подбор палитры: k-means с привязкой к сетке Вектора и региональными ограничениями
# ----------------------------------------------------------------------------

VEC_G = VEC_RGB / 255.0                      # сетка Вектора в перцептивном (гамма) пространстве


def snap_g(colors_g):
    d = dist2(np.asarray(colors_g).reshape(-1, 3), VEC_G)
    return np.argmin(d, axis=1)


# --- перцептивное пространство OkLab (второй заход, §8 TITLE.md) ------------
#
# Зачем: у Вектора на синий ДВА бита (0, 85, 170, 255). Насыщенный оранжевый
# картон обложки хочет синий ~45, и выбор между 0 и 85 решается метрикой.
# Взвешенный евклид в гамме (R:G:B = 2:4:3) даёт синему столько веса, что
# ярким местам картона достаётся 0x56 (218,72,85) — розовый, а соседним
# 0x15 (182,72,0) — оранжевый: на экране это пятно другого тона (замерено:
# пятно 90x240 точек слева от логотипа). OkLab меряет уход по цветности
# отдельно от светлоты и такой обмен запрещает.

def oklab(lin):
    """Линейный sRGB 0..1 -> OkLab (Bjorn Ottosson, 2020)."""
    a = np.asarray(lin, dtype=np.float64)
    l = 0.4122214708 * a[..., 0] + 0.5363325363 * a[..., 1] + 0.0514459929 * a[..., 2]
    m = 0.2119034982 * a[..., 0] + 0.6806995451 * a[..., 1] + 0.1073969566 * a[..., 2]
    s = 0.0883024619 * a[..., 0] + 0.2817188376 * a[..., 1] + 0.6299787005 * a[..., 2]
    l_, m_, s_ = np.cbrt(l), np.cbrt(m), np.cbrt(s)
    return np.stack([
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    ], axis=-1)


VEC_OK = oklab(VEC_LIN)
SPACE = 'gamma'                              # ставится ключом --space в main()


def perc(srgb01):
    """sRGB 0..1 -> координаты того пространства, в котором меряем расстояния."""
    return srgb01 if SPACE == 'gamma' else oklab(np.power(np.clip(srgb01, 0, 1), GAMMA))


def vec_perc():
    return VEC_G if SPACE == 'gamma' else VEC_OK


def dist2p(a, b):
    """Расстояние в выбранном пространстве: в гамме — взвешенное, в OkLab — нет
    (веса каналов уже зашиты в само преобразование)."""
    if SPACE == 'gamma':
        return dist2(a, b)
    d = a[..., None, :] - b[None, ...] if a.ndim == 2 else a[..., None, :] - b
    return np.einsum('...c,...c->...', d, d)


def snap_p(colors_p):
    return np.argmin(dist2p(np.asarray(colors_p).reshape(-1, 3), vec_perc()), axis=1)


def pick_nearest(pal_lin, sub, lin_colors):
    """Ближайшие цвета палитры к точкам (обе стороны — в линейном свете)."""
    if SPACE == 'gamma':
        return sub[np.argmin(dist2(lin_colors, pal_lin[sub]), axis=-1)]
    a = oklab(lin_colors)
    b = oklab(np.clip(pal_lin[sub], 0, 1))
    d = a[..., None, :] - b
    return sub[np.argmin(np.einsum('...c,...c->...', d, d), axis=-1)]


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
    px = perc(frame.reshape(-1, 3) / 255.0)
    npin = len(pins)
    if region_limit is not None:
        rmask = region_limit[0].reshape(-1)
        k = region_limit[1]
    else:
        rmask, k = None, n

    cent = np.zeros((n, 3))
    for i, b in enumerate(pins):
        cent[i] = vec_perc()[b]
    # k-means++ для свободных мест
    have = list(cent[:npin]) if npin else [px[rng.integers(len(px))]]
    start = npin if npin else 1
    if not npin:
        cent[0] = have[0]
    for i in range(start, n):
        d = np.min(dist2p(px, np.array(have)), axis=1)
        p = d / max(d.sum(), 1e-12)
        cent[i] = px[rng.choice(len(px), p=p)]
        have.append(cent[i])

    for it in range(iters):
        d = dist2p(px, cent)
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
            new[npin:] = vec_perc()[snap_p(new[npin:])]
        if np.allclose(new, cent):
            cent = new
            break
        cent = new
    return snap_p(cent)


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
        out[m] = pick_nearest(pal, sub, img_lin[m])
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
            i = int(pick_nearest(pal, sub, old[None, :])[0])
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
    global ALLOW_SETS
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
    ap.add_argument('--native', action='store_true',
                    help='снять родную сетку C64 (160x200) и пересчитать пиксель-арт-аккуратно, '
                         'без размытия: только так штрихи готики остаются чёткими')
    ap.add_argument('--resample', choices=['nearest', 'majority', 'letterbox', 'smooth'],
                    default='nearest', help='--native: как класть сетку на 256x256. smooth — '
                    'гладко увеличить уже ЧИСТУЮ сетку и заново привязать к тем же 15 цветам: '
                    'при 160->256 (x1.6) ячейка ложится то в одну, то в две точки, и ровные '
                    'штрихи готики идут «чёрточками» — сглаживание это разбивает')
    ap.add_argument('--smooth-filter', dest='sfilter', choices=['bilinear', 'lanczos', 'box2x'],
                    default='bilinear', help='--resample smooth: чем увеличивать')
    ap.add_argument('--blur', type=float, default=0.0,
                    help='--resample smooth: доп. размытие в точках экрана перед привязкой')
    ap.add_argument('--base', default='title16',
                    help='основа имён выходных файлов для c16')
    ap.add_argument('--palette-name', dest='palname', choices=sorted(NAMED_PALETTES) + ['c64'],
                    default='c64', help='--native: именованная палитра Вектора вместо привязки '
                                        'цветов исходника к ближайшим')
    ap.add_argument('--tries', type=int, default=8,
                    help='c8/c16: сколько запусков k-means с разными зёрнами; берётся лучший')
    ap.add_argument('--search', action='store_true',
                    help='a4: полный перебор недостающих цветов вместо k-means')
    ap.add_argument('--b53fit', choices=['right-first', 'joint'], default='right-first')
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--no-preview', action='store_true')
    # --- второй заход: живописные исходники и ZX-заставка (§8 TITLE.md) ------
    ap.add_argument('--crop', default=None,
                    help='кусок исходника L,T,R,B в его собственных точках')
    ap.add_argument('--fit', choices=['stretch', 'height', 'width'], default=None,
                    help='как вписать кусок в 256xrows с поправкой на пиксель 4/3')
    ap.add_argument('--bg', default='0,0,0', help='цвет полей при --fit height/width')
    ap.add_argument('--zx', action='store_true',
                    help='исходник — .scr ZX-Spectrum 6912 Б: 256 точек по горизонтали '
                         'один к одному, 192 -> rows по вертикали')
    ap.add_argument('--zx-resample', dest='zxres', choices=['nearest', 'bilinear'],
                    default='nearest')
    ap.add_argument('--space', choices=['gamma', 'oklab'], default='gamma',
                    help='пространство, в котором подбирается палитра и ищется ближайший цвет')
    ap.add_argument('--zx-palette', dest='zxpal', choices=['native', 'fit'], default='native',
                    help='native — 15 цветов ZX, привязанных к сетке 3-3-2 Вектора')
    args = ap.parse_args()
    globals()['SPACE'] = args.space

    os.makedirs(args.out, exist_ok=True)
    tag = args.tag or f'{args.mode}_{args.dither}'

    if args.zx:
        return run_zx(args, ap, tag)

    if args.crop or args.fit:
        crop = tuple(int(v) for v in args.crop.split(',')) if args.crop else None
        bg = tuple(int(v) for v in args.bg.split(','))
        frame, geom = build_frame_crop(args.src, crop, args.fit or 'stretch', args.filter,
                                       args.rows, bg, args.ox, args.oy)
    else:
        frame, geom = build_frame(args.src, args.scale, args.filter, args.rows, args.ox, args.oy)
    img_lin = to_lin(frame)

    if args.native:
        if args.mode != 'c16':
            ap.error('--native сделан для --mode c16 (16 цветов = ровно набор исходника)')
        nat = native_grid(args.src)
        if args.resample == 'smooth':
            # Увеличиваем ЧИСТУЮ сетку (не исходник!), потом заново привязываем
            # к той же таблице из 15 цветов — тогда любая именованная палитра
            # подходит без переделки, меняются только планы.
            rgb = Image.fromarray(NATIVE_RGB[nat].astype(np.uint8))
            if args.sfilter == 'box2x':
                rgb = rgb.resize((320, 400), Image.NEAREST).resize((256, args.rows), Image.BOX)
            else:
                rgb = rgb.resize((256, args.rows),
                                 Image.LANCZOS if args.sfilter == 'lanczos' else Image.BILINEAR)
            if args.blur > 0:
                rgb = rgb.filter(ImageFilter.GaussianBlur(args.blur))
            sm = np.asarray(rgb, dtype=np.float64)
            ALLOW_SETS = {0: [True] * len(NATIVE_COLORS)}
            allowed = np.zeros((args.rows, 256), dtype=np.int8)
            npal = to_lin(NATIVE_RGB)
            if args.strength > 0:
                ci = quant_fs(to_lin(sm), npal, allowed, args.strength)
            else:
                ci = quant_none(to_lin(sm), npal, allowed)
            ci = ci.astype(np.int16)
        elif args.resample == 'majority':
            # ближайший-по-центру и мажоритарный расходятся не на ничьих, а там,
            # где две-три соседние ячейки одного цвета складываются и перевешивают
            # ту, что накрыла центр: ~1.5 % точек
            ci = majority_resample(nat, args.rows, len(NATIVE_COLORS)).astype(np.int16)
        else:
            ci = native_to_frame(nat, args.resample, args.rows).astype(np.int16)
        if args.palname == 'c64':
            pal_b = np.array(snap_g(NATIVE_RGB / 255.0))      # цвета исходника как есть
        else:
            pal_b = np.array(NAMED_PALETTES[args.palname], dtype=int)
        pal_b = np.concatenate([pal_b, np.zeros(16 - len(pal_b), dtype=int)])
        pal = VEC_LIN[pal_b]
        # эталон «сетка в собственных цветах» — им меряется только потеря на палитре
        ref = NATIVE_RGB[np.clip(ci, 0, len(NATIVE_RGB) - 1)]
        out_srgb = to_srgb(pal[ci])
        blur0 = lambda a: np.asarray(Image.fromarray(np.uint8(np.clip(a, 0, 255)))
                                     .filter(ImageFilter.GaussianBlur(1.0)), dtype=np.float64)
        print(f'[{tag}] родная сетка {nat.shape[1]}x{nat.shape[0]} -> {args.resample}, '
              f'палитра {args.palname}: '
              f'RMSE vs исходник {np.sqrt(np.mean((out_srgb - frame) ** 2)):.2f}, '
              f'после размытия {np.sqrt(np.mean((blur0(out_srgb) - blur0(frame)) ** 2)):.2f}, '
              f'RMSE vs сетка-в-своих-цветах {np.sqrt(np.mean((out_srgb - ref) ** 2)):.2f}')
        pal_bytes = pal_b
        used = sorted(set(int(v) for v in np.unique(ci)))
        print('  палитра:', ', '.join(
            f'{NATIVE_COLORS[c][0]}=0x{pal_bytes[c]:02X}{vec_byte_to_rgb(int(pal_bytes[c]))}'
            for c in used))
        emit(args, tag, ci, pal_bytes, p2len_out=[0])
        return

    right = np.zeros((args.rows, 256), dtype=bool)
    right[:, args.split:] = True

    # --- палитра ------------------------------------------------------------
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
            px = perc(frame.reshape(-1, 3) / 255.0)
            best = None
            for k in range(args.tries):
                cand = fit_palette(frame, n, pins=pins, seed=args.seed + k)
                e = float(dist2p(px, vec_perc()[cand]).min(axis=1).mean())
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
    # перцептивная мера: средний модуль разности в OkLab ПОСЛЕ размытия — то,
    # что видит глаз. RMSE в sRGB систематически хвалит ту палитру, которую
    # и подбирали в гамме, поэтому одной им мерить нельзя (см. §8 TITLE.md)
    de = float(np.mean(np.sqrt(np.sum((oklab(to_lin(blur(out_srgb)))
                                       - oklab(to_lin(blur(frame)))) ** 2, axis=-1)))) * 1000
    print(f'[{tag}] кадр {geom[2]}x{geom[3]} @ ({geom[0]},{geom[1]}), '
          f'RMSE = {err:.2f}, RMSE после размытия = {errb:.2f}, dE(OkLab)x1000 = {de:.1f}')

    pal_bytes = snap(pal.reshape(-1, 3))
    used = sorted(set(int(v) for v in np.unique(ci)))
    print('  палитра:', ', '.join(
        f'c{c}=0x{pal_bytes[c]:02X}{vec_byte_to_rgb(int(pal_bytes[c]))}' for c in used))

    # --- планарные файлы ----------------------------------------------------
    emit(args, tag, ci, pal_bytes)


def run_zx(args, ap, tag):
    """ZX-заставка .scr -> 16 цветов Вектора.

    Горизонталь один к одному (256 = 256), вертикаль 192 -> rows. При rows=256
    это ровно x4/3: три исходные строки дают четыре экранных, и рисунок
    ложится регулярно — той неравномерности, которой рябил путь от C64 (x1.6),
    здесь нет по построению.
    """
    global ALLOW_SETS
    if args.mode != 'c16':
        ap.error('--zx сделан для --mode c16')
    nat = read_scr(args.src)                            # (192,256) номера цветов ZX
    rgb = np.array(ZX_PAL, dtype=np.float64)

    used = sorted(set(int(v) for v in np.unique(nat)))  # реально встретившиеся цвета ZX
    if len(used) > 16:
        ap.error('в .scr больше 16 цветов — не бывает')
    remap = {c: i for i, c in enumerate(used)}
    idx = np.vectorize(remap.get)(nat).astype(np.int16)

    if args.zxres == 'nearest':
        # каждая экранная строка берёт ту исходную, что накрыла её центр
        ys = np.minimum(((np.arange(args.rows) + 0.5) * 192.0 / args.rows).astype(int), 191)
        ci = idx[ys]
        ref = rgb[np.array(used)][ci]
    else:
        # билинейно по вертикали в цвете, потом обратно к тем же 15 цветам
        im = Image.fromarray(rgb[nat].astype(np.uint8)).resize((256, args.rows), Image.BILINEAR)
        sm = np.asarray(im, dtype=np.float64)
        ALLOW_SETS = {0: [True] * len(used)}
        allowed = np.zeros((args.rows, 256), dtype=np.int8)
        pl = to_lin(rgb[np.array(used)])
        ci = (quant_fs(to_lin(sm), pl, allowed, args.strength) if args.strength > 0
              else quant_none(to_lin(sm), pl, allowed)).astype(np.int16)
        ref = sm

    if args.palette:
        pal_b = np.array([int(v, 0) for v in args.palette.split(',')])
    elif args.zxpal == 'native':
        pal_b = np.array(snap_g(rgb[np.array(used)] / 255.0))
    else:
        pal_b = fit_palette(rgb[np.array(used)][ci], len(used), seed=args.seed)
    pal_b = np.concatenate([pal_b, np.zeros(16 - len(pal_b), dtype=int)])

    out_srgb = to_srgb(VEC_LIN[pal_b][ci])
    blur = lambda a: np.asarray(Image.fromarray(np.uint8(np.clip(a, 0, 255)))
                                .filter(ImageFilter.GaussianBlur(1.0)), dtype=np.float64)
    print(f'[{tag}] ZX 256x192 -> 256x{args.rows} ({args.zxres}), цветов {len(used)}: '
          f'RMSE vs эталон {np.sqrt(np.mean((out_srgb - ref) ** 2)):.2f}, '
          f'после размытия {np.sqrt(np.mean((blur(out_srgb) - blur(ref)) ** 2)):.2f}')
    emit(args, tag, ci, pal_b, p2len_out=[0])
    return


def emit(args, tag, ci, pal_bytes, p2len_out=None):
    """Записать планы, палитру и превью."""
    p0, p1, p2, p3 = encode_planes(ci, args.mode, args.rows)
    pal16 = build_palette(args.mode, pal_bytes)
    p2_len = 8192 if args.mode in ('c8', 'c16') else 0

    if args.mode == 'c16':
        base = os.path.join(args.out, args.base + (f'_{args.tag}' if args.tag else ''))
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
