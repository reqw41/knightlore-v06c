#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mkrelease.py — релизный образ: 16-цветная заставка + упакованная игра.

    python3 tools/mkrelease.py build/knightlore_vec.r0m build/knightlore_title.asm

Берёт ГОТОВЫЙ образ цели vec (он не меняется ни на байт), пакует его содержимое
в ZX0 (tools/zx0pack.py) и выдаёт исходник первой стадии, который sjasmplus
собирает в build/knightlore_title.r0m.

Раскладка релизного образа в момент загрузки:
    0x0000..           первая стадия: загрузчик, ISR, палитра, звук, распаковщик
    ...                поток A (0xA000..0xF0FF игры), хвост потока B (256 Б)
    BSRC..0x7FFF       тело потока B (нижние 32К игры), КОНЧАЕТСЯ ровно на 0x7FFF
    0x8000..0xFFFF     четыре плоскости заставки (16 цветов), 32 768 Б как есть

Что делает первая стадия: ставит палитру заставки (в кадровом гашении, все 16 записей
каждый кадр), играет мелодию menu на ВИ53 штатным драйвером MSX, ждёт клавишу матрицы
или кнопку джойстика (порт 0x0E), ждёт отпускания, гасит экран палитрой, глушит ВИ53,
копирует распаковщик в 0xF100 (эта область в игре — таблицы сдвига, их строит START)
и распаковывает:
    1) поток A -> 0xA000..0xF0FF   (плоскости 1/2 вне картинки и верхний регион);
    2) хвост потока B -> 0x8000    (256 Б, запас для распаковки «на месте»);
    3) поток B -> 0x0000..0x7FFF   НА МЕСТЕ: поток кончается на 0x80FF, то есть на 256 Б
       выше конца распакованного, а запас ZX0 (delta) на порядок меньше — ASSERT ниже;
    4) обнуляет плоскость 0 (в образе vec она нулевая) и уходит на 0x0000.
После этого память байт в байт равна образу vec в момент запуска, и дальше играет он.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import zx0pack                       # noqa: E402
import kl_sound_ay                   # noqa: E402

ROOT = os.path.dirname(HERE)
TITLE_DIR = os.path.join(ROOT, 'src')

P0 = 0x8000
P1 = 0xA000
P2 = 0xC000
P3 = 0xE000
TABLES = 0xF100                  # 0xF100..0xFFFF — таблицы сдвига, строит build_lookup_tbls
UPPER_END = TABLES               # конец потока A
TAIL = 256                       # хвост потока B, уезжающий в плоскость 0
RUN_ADDR = 0xF100                # куда копируется распаковщик перед работой


# ---------------------------------------------------------------------------
# первая стадия
# ---------------------------------------------------------------------------

STAGE1 = r'''; --------------------------------------------------------------------------
; knightlore_title.asm — РЕЛИЗНЫЙ образ: заставка + упакованная игра.
; Сгенерирован tools/mkrelease.py, править надо генератор.
; Всё в подмножестве i8080 (гейт tools/check_i8080.py).
; --------------------------------------------------------------------------

	ORG	0x0000
		di
		ld	sp, TSTACK
		jp	t_boot
	ORG	0x0038
		jp	t_isr			; RST 38h: кадровое прерывание

	ORG	0x0040
t_pal:		DS	16			; палитра заставки, ISR льёт её каждый кадр
t_keys:		DB	0			; клавиатура+джойстик: 0 = ничего не нажато
graphic_objs_tbl:	DS	3		; заглушка: драйвер эффектов её ЧИТАЕТ (эффектов в заставке нет)

; ---- загрузка ------------------------------------------------------------
t_boot:		ld	a, 0x88			; 8255: порт A выход (скролл/ряд клавиатуры), порт B выход (бордюр)
		out	(0x00), a		; управляющее слово ОБНУЛЯЕТ порты 1..3 — скролл ставим после него
		xor	a
		out	(0x02), a		; бордюр чёрный, режим 256
		ld	a, 0xFF
		out	(0x03), a		; скролл: строка 255 наверху
		ld	hl, t_palsrc		; палитра заставки -> рабочая копия
		ld	de, t_pal
		ld	b, 16
tb_pal:		ld	a, (hl)
		ld	(de), a
		inc	hl
		inc	de
		dec	b
		jp	nz, tb_pal
		call	msx_snd_init		; зеркало AY, слоты, заготовка OUT для ВИ53
		ei
		ld	hl, msx_hdr_menu	; мелодия меню — та же, что в игре
		call	msx_play_tune
tb_rel:		ld	a, (t_keys)		; образ могли загрузить с зажатой клавишей —
		or	a			; сначала дождаться, пока всё отпущено
		jp	nz, tb_rel
; ---- ожидание: клавиша матрицы или кнопка джойстика ----------------------
t_wait:		ld	a, (t_keys)
		or	a
		jp	nz, t_go
		ld	a, (msx_play)		; мелодия кончилась — начать заново (как в меню)
		or	a
		jp	nz, t_wait
		ld	hl, msx_hdr_menu
		call	msx_play_tune
		jp	t_wait
; ---- снятие: дождаться отпускания, погасить экран, заглушить звук --------
t_go:		ld	a, (t_keys)		; антидребезг: клавиша, которой сняли заставку,
		or	a			; не должна попасть в меню как нажатая
		jp	nz, t_go
		ld	hl, t_pal		; палитра в ноль: экран чёрный
		ld	b, 16
tg_blk:		ld	(hl), 0
		inc	hl
		dec	b
		jp	nz, tg_blk
		ld	b, 3			; три кадра: ISR успевает залить чёрное
tg_wait:	ld	a, (t_frame)
tg_w1:		ld	c, a
		ld	a, (t_frame)
		cp	c
		jp	z, tg_w1
		dec	b
		jp	nz, tg_wait
		di
		ld	a, 0x30			; ВИ53: все три счётчика — режим 0 без делителя = молчат
		out	(0x08), a
		ld	a, 0x70
		out	(0x08), a
		ld	a, 0xB0
		out	(0x08), a
		ld	hl, t_run_src		; распаковщик -> 0xF100 (в игре там таблицы сдвига,
		ld	de, RUN_ADDR		; их строит build_lookup_tbls уже после нас)
		ld	bc, t_run_len
tg_cp:		ld	a, (hl)
		ld	(de), a
		inc	hl
		inc	de
		dec	bc
		ld	a, b
		or	c
		jp	nz, tg_cp
		jp	RUN_ADDR

; ---- кадровое прерывание -------------------------------------------------
; Палитра грузится ЦЕЛИКОМ каждый кадр: запись по порту 0x0C защёлкивается в ту запись,
; чей индекс сейчас под лучом, поэтому перед каждым цветом ставится бордюр = индекс.
; Четыре OUT подряд — как в игре (одиночный может не попасть в обратный ход).
t_isr:		push	af
		push	bc
		push	de
		push	hl
		ld	hl, t_pal
		ld	c, 0
ti_pal:		ld	a, c
		out	(0x02), a
		ld	a, (hl)
		inc	hl
		out	(0x0C), a
		out	(0x0C), a
		out	(0x0C), a
		out	(0x0C), a
		inc	c
		ld	a, c
		cp	16
		jp	nz, ti_pal
		xor	a
		out	(0x02), a		; бордюр обратно чёрный
		ld	hl, t_frame
		inc	(hl)
		call	t_scan			; клавиатура и джойстик
		call	msx_mtick		; мелодия: один тик = один кадр
		call	msx_viflush		; зеркало регистров AY -> ВИ53
		pop	hl
		pop	de
		pop	bc
		pop	af
		ei
		ret
t_frame:	DB	0

; ---- опрос: восемь рядов матрицы + кнопки джойстика ----------------------
; Ряд выбирается активным нулём в порту A (он же регистр скролла — вернуть 0xFF!),
; столбцы читаются из порта B (активный ноль -> cpl). Джойстик: порт 0x0E, кнопки 0xC0.
t_scan:		ld	a, 0x8A			; порт B на ВХОД
		out	(0x00), a
		ld	c, 0xFE			; маска ряда 0
		ld	b, 8
		ld	e, 0			; накопитель «что-нибудь нажато»
ts_row:		ld	a, c
		out	(0x03), a
		in	a, (0x02)
		cpl
		or	e
		ld	e, a
		ld	a, c
		rlca				; 0xFE -> 0xFD -> 0xFB ...
		ld	c, a
		dec	b
		jp	nz, ts_row
		ld	a, 0x88			; порт B обратно на выход (иначе не пишутся бордюр и палитра)
		out	(0x00), a
		ld	a, 0xFF
		out	(0x03), a		; скролл на место
		in	a, (0x0E)		; джойстик: активный ноль
		cpl
		and	0xC0			; FIRE1/FIRE2
		or	e
		ld	(t_keys), a
		ret

'''

RUNNER = r'''; ---- распаковщик: работает из 0xF100, когда низ уже не жилец ------------
t_run_src:
	DISP	RUN_ADDR
t_run:		ld	sp, 0xFFF0		; стек — выше распаковщика, ниже 0xFFFF
		ld	hl, STREAM_A		; 1. плоскости 1/2 вне картинки + верхний регион
		ld	de, 0xA000
		call	dz
		ld	hl, TAIL_SRC		; 2. хвост потока B -> плоскость 0 (запас «на месте»)
		ld	de, 0x8000
		ld	b, 0			; 256 байт
tr_tail:	ld	a, (hl)
		ld	(de), a
		inc	hl
		inc	de
		dec	b
		jp	nz, tr_tail
		ld	hl, STREAM_B		; 3. нижние 32К — НА МЕСТЕ (поток кончается на 0x80FF)
		ld	de, 0x0000
		call	dz
		ld	hl, 0x8000		; 4. плоскость 0 в ноль (в образе vec она нулевая)
		ld	bc, 0x2000
tr_clr:		ld	(hl), 0
		inc	hl
		dec	bc
		ld	a, b
		or	c
		jp	nz, tr_clr
		jp	0x0000			; в игру: di; ld sp; jp vec_boot

; ---- ZX0, распаковка вперёд: HL = поток, DE = приёмник -------------------
; Раскладка регистров на всё время работы:
;   A  — резервуар битов (в нём же маркерный бит, как в эталонном dzx0);
;   BC — указатель потока (чтение ld a,(bc) — у i8080 есть);
;   DE — ПРИЁМНИК, не уходит в память ни разу;
;   HL — накопитель гамма-кода, он же счётчик копии.
; У i8080 нет ни ldir, ни rl c/rl b, поэтому 16-битный сдвиг с внесением бита сделан
; ветвлением add hl,hl / add hl,hl+inc hl — A при этом цел. Если держать резервуар
; в памяти, бит стоит на 26 тактов дороже (ld a,(nn) + ld (nn),a), а бит здесь
; читается около 41 000 раз (замер §33).
dz:		ld	b, h
		ld	c, l			; BC = поток
		ld	hl, 1
		ld	(dz_off), hl		; прошлое смещение = 1
		ld	a, 0x80			; резервуар пуст: маркерный бит
; ---- литералы: HL байт из потока прямо в приёмник
dz_lit:		call	dz_elias
		ld	(dz_bits), a
		ld	a, l			; счётчик по двум байтам: 39 тактов на байт
		or	a			; вместо 48 у проверки ld a,h / or l
		jp	z, dl_go
		inc	h
dl_go:		ld	a, (bc)
		inc	bc
		ld	(de), a
		inc	de
		dec	l
		jp	nz, dl_go
		dec	h
		jp	nz, dl_go
		ld	a, (dz_bits)
		add	a, a			; бит: 0 = копия с прошлым смещением, 1 = новое
		jp	nz, dz_g1
		ld	a, (bc)
		inc	bc
		rla
dz_g1:		jp	c, dz_new
		call	dz_elias		; копия с ПРОШЛЫМ смещением
		call	dz_copy
		add	a, a
		jp	nz, dz_g2
		ld	a, (bc)
		inc	bc
		rla
dz_g2:		jp	nc, dz_lit
; ---- копия с новым смещением
dz_new:		call	dz_elias_inv		; старший байт смещения (биты инвертированы)
		inc	h
		dec	h
		ret	nz			; 256 — конец потока
		ld	(dz_bits), a
		ld	a, (bc)			; младший байт смещения; его бит 0 — первый бит длины
		inc	bc
		ld	(dz_lsb), a
		add	hl, hl			; смещение = MSB*128 - (LSB>>1)
		add	hl, hl
		add	hl, hl
		add	hl, hl
		add	hl, hl
		add	hl, hl
		add	hl, hl
		ld	a, (dz_lsb)
		and	0xFE			; AND сбрасывает перенос — rra даст ноль в бите 7
		rra
		push	bc			; поток на стек: C нужен под вычитание
		ld	c, a
		ld	b, 0
		ld	a, l
		sub	c
		ld	l, a
		ld	a, h
		sbc	a, b
		ld	h, a
		pop	bc
		ld	(dz_off), hl
		ld	hl, 1			; длина-1: первый бит уже прочитан из младшего байта
		ld	a, (dz_lsb)
		rra				; бит 0 -> перенос
		ld	a, (dz_bits)		; ld a,(nn) флаги не трогает — перенос жив
		jp	c, dz_lend
dz_lenl:	add	a, a			; бит данных
		jp	nz, dz_g3
		ld	a, (bc)
		inc	bc
		rla
dz_g3:		jp	c, dz_l1
		add	hl, hl
		jp	dz_lm
dz_l1:		add	hl, hl
		inc	hl
dz_lm:		add	a, a			; маркер
		jp	nz, dz_g4
		ld	a, (bc)
		inc	bc
		rla
dz_g4:		jp	nc, dz_lenl
dz_lend:	inc	hl
		call	dz_copy
		add	a, a			; бит: 0 = дальше литералы, 1 = снова новое смещение
		jp	nz, dz_g5
		ld	a, (bc)
		inc	bc
		rla
dz_g5:		jp	c, dz_new
		jp	dz_lit

; копия HL байт из (приёмник - смещение) в приёмник; перекрытие законно (это и есть RLE)
dz_copy:	ld	(dz_bits), a
		push	bc			; поток на стек
		ld	b, h
		ld	c, l			; BC = длина
		ld	hl, (dz_off)
		ld	a, e
		sub	l
		ld	l, a
		ld	a, d
		sbc	a, h
		ld	h, a			; HL = источник
		ld	a, c
		or	a
		jp	z, dc_l
		inc	b
dc_l:		ld	a, (hl)
		ld	(de), a
		inc	hl
		inc	de
		dec	c
		jp	nz, dc_l
		dec	b
		jp	nz, dc_l
		pop	bc			; поток обратно
		ld	a, (dz_bits)
		ret

; переслоенный гамма-код -> HL (>= 1); A = резервуар, BC = поток
dz_elias:	ld	hl, 1
de_l:		add	a, a			; маркер
		jp	nz, de_m
		ld	a, (bc)
		inc	bc
		rla
de_m:		ret	c
		add	a, a			; бит данных
		jp	nz, de_d
		ld	a, (bc)
		inc	bc
		rla
de_d:		jp	c, de_one
		add	hl, hl
		jp	de_l
de_one:		add	hl, hl
		inc	hl
		jp	de_l

; то же, но биты данных инвертированы (так пишется старший байт смещения)
dz_elias_inv:	ld	hl, 1
di_l:		add	a, a
		jp	nz, di_m
		ld	a, (bc)
		inc	bc
		rla
di_m:		ret	c
		add	a, a
		jp	nz, di_d
		ld	a, (bc)
		inc	bc
		rla
di_d:		jp	c, di_zero
		add	hl, hl
		inc	hl
		jp	di_l
di_zero:	add	hl, hl
		jp	di_l

dz_bits:	DB	0			; резервуар битов на время копий
dz_off:		DW	1			; прошлое смещение
dz_lsb:		DB	0
	ENT
t_run_len:	equ	$ - t_run_src
'''


def emit(rom_path, out_path):
    rom = open(rom_path, 'rb').read()
    assert len(rom) == 0x10000, 'образ vec обязан быть ровно 64К'
    assert set(rom[P0:P1]) == {0}, 'плоскость 0 в образе vec не нулевая'
    assert set(rom[TABLES:]) == {0}, 'область таблиц сдвига 0xF100.. в образе vec не нулевая'

    stream_a, delta_a = zx0pack.compress(rom[P1:UPPER_END])
    stream_b, delta_b = zx0pack.compress(rom[0:P0])
    assert zx0pack.decompress(stream_a) == rom[P1:UPPER_END]
    assert zx0pack.decompress(stream_b) == rom[0:P0]

    # поток B кончается на 0x8000+TAIL-1, то есть на TAIL байт выше конца распакованного:
    # это и есть запас для распаковки «на месте» (ZX0 требует не меньше delta)
    assert delta_b <= TAIL, 'запас потока B меньше delta: %d > %d' % (delta_b, TAIL)
    bsrc = P0 + TAIL - len(stream_b)
    assert bsrc > 0, 'поток B не помещается в нижние 32К'
    body = stream_b[:P0 - bsrc]
    tail = stream_b[P0 - bsrc:]
    assert len(tail) == TAIL

    build = os.path.join(ROOT, 'build')
    open(os.path.join(build, 'title_a.zx0'), 'wb').write(stream_a)
    open(os.path.join(build, 'title_b_body.zx0'), 'wb').write(body)
    open(os.path.join(build, 'title_b_tail.zx0'), 'wb').write(tail)

    src = ['\tDEVICE NOSLOT64K\n']
    src.append('RUN_ADDR\tequ\t0x%04X\n' % RUN_ADDR)
    src.append('STREAM_A\tequ\tt_stream_a\n')
    src.append('TAIL_SRC\tequ\tt_stream_bt\n')
    src.append('STREAM_B\tequ\t0x%04X\n' % bsrc)
    src.append(STAGE1)
    src.append('; ================= звук: драйвер MSX как в игре =================\n')
    src.append(kl_sound_ay.DRIVER)
    src.append(kl_sound_ay.data_block())
    src.append('\n\t\tDS\t128\t\t\t; стек первой стадии растёт вниз отсюда\nTSTACK:\n')
    src.append(RUNNER)
    src.append('\nt_palsrc:\tINCBIN\t"%s"\n' % os.path.join('src', 'title.pal'))
    src.append('t_stream_a:\tINCBIN\t"build/title_a.zx0"\n')
    src.append('t_stream_bt:\tINCBIN\t"build/title_b_tail.zx0"\n')
    src.append('\t\tASSERT $ <= 0x%04X\t; первая стадия + поток A + хвост не доходят до тела потока B\n' % bsrc)
    src.append('\tORG\t0x%04X\n' % bsrc)
    src.append('t_stream_b:\tINCBIN\t"build/title_b_body.zx0"\n')
    src.append('\t\tASSERT $ == 0x%04X\t; тело потока B кончается ровно на 0x8000\n' % P0)
    for base, name in ((P0, 'title_p0.bin'), (P1, 'title_p1.bin'),
                       (P2, 'title_p2.bin'), (P3, 'title_p3.bin')):
        src.append('\tORG\t0x%04X\n\tINCBIN\t"%s"\n' % (base, os.path.join('src', name)))
    src.append('\tSAVEBIN\t"build/knightlore_title.r0m", 0, 0x10000\n')
    open(out_path, 'w').write(''.join(src))

    print('mkrelease: поток A %d -> %d Б (delta %d), поток B %d -> %d Б (delta %d)'
          % (UPPER_END - P1, len(stream_a), delta_a, P0, len(stream_b), delta_b))
    print('mkrelease: тело потока B с 0x%04X, хвост %d Б -> 0x8000; под первую стадию 0x%04X байт'
          % (bsrc, TAIL, bsrc))
    return bsrc, len(stream_a), len(stream_b)


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 1
    emit(sys.argv[1], sys.argv[2])
    return 0


if __name__ == '__main__':
    sys.exit(main())
