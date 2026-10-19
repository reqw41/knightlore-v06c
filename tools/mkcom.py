#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mkcom.py — снимок памяти 64 КБ (грузится с 0x0000) -> обычный образ Вектора с 0x0100.

    python3 tools/mkcom.py <снимок.r0m> <выход.asm> [END=0xF100]

Зачем. Релизные образы (.c00/.r0m) — снимок всей памяти, их кладут с адреса 0. Загрузчики,
которые кладут программу с 0x0100 (обычный .rom Вектора, CP/M-загрузчик Турбо+ с ключом -V),
такой снимок не возьмут: игра занимает и нулевую страницу. Этот образ сам раскладывает снимок.

Раскладка в момент загрузки (с 0x0100):
    стартовый код | распаковщик (копия) | поток ZX0 снимка [0, END)
Что делает:
    1) di, SP = 0 (стек в 0xFFxx), копирует распаковщик в RUN_ADDR (0xFD00);
    2) распаковщик поднимает поток к верху: он кончается на END + запас, где запас — то,
       что ZX0 требует для распаковки «на месте» (zx0pack.compress считает его моделью);
    3) распаковывает поток вперёд в 0x0000..END-1 и уходит на 0x0000 — как после сброса.
Область END..0xFFFF не восстанавливается: это таблицы сдвига, которые START строит заново
(build_lookup_tbls) до первого чтения — на этом же держатся релизные образы с заставкой.
После распаковки память [0, END) байт в байт равна снимку; стенд — tools/comgate_vec.js.
Распаковщик — копия из tools/mkrelease.py Knight Lore (RUNNER), его сверяет tools/packgate_vec.js.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import zx0pack                       # noqa: E402

LOAD = 0x0100
RUN_ADDR = 0xFD00                    # распаковщик и его переменные; стек — выше, к 0xFFFF


# Распаковщик ZX0 (подпрограмма dz и её переменные) — копия из tools/mkrelease.py (RUNNER)
# Knight Lore, где его сверяет с упаковщиком tools/packgate_vec.js. Если рядом есть
# mkrelease.py, копия сверяется с ним при каждом запуске.
DECODER = r'''; ---- ZX0, распаковка вперёд: HL = поток, DE = приёмник -------------------
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
'''


def decoder_text():
    try:
        import mkrelease
    except ImportError:
        return DECODER
    r = mkrelease.RUNNER
    assert r[r.index('; ---- ZX0, распаковка вперёд'):r.index('\tENT')] == DECODER, \
        'распаковщик в mkcom.py разошёлся с mkrelease.py'
    return DECODER


def emit(snap_path, out_path, end=0xF100):
    snap = open(snap_path, 'rb').read()
    assert len(snap) == 0x10000, 'снимок обязан быть ровно 64 КБ'
    target = snap[0:end]
    stream, delta = zx0pack.compress(target)
    assert zx0pack.decompress(stream) == target
    n = len(stream)
    margin = delta + 2                    # запас «на месте» с округлением вверх
    dst = end + margin - n                # поток кончается на end + margin
    assert end + margin <= RUN_ADDR, 'хвост потока наехал бы на распаковщик'
    src = [
        '; Сгенерирован tools/mkcom.py из %s — править генератор.\n' % os.path.basename(snap_path),
        '; Снимок 64 КБ, упакованный в образ с 0x0100 (см. шапку mkcom.py). Подмножество i8080.\n',
        '\t\tDEVICE\tNOSLOT64K\n',
        'STREAM_N\tEQU\t%d\n' % n,
        'STREAM_DST\tEQU\t0x%04X\t; поток после подъёма: кончается на 0x%04X (END + запас %d)\n'
        % (dst, end + margin, margin),
        'RUN_ADDR\tEQU\t0x%04X\n' % RUN_ADDR,
        '\t\tORG\t0x%04X\n' % LOAD,
        'c_start:\tdi\n',
        '\t\tld\tsp, 0\t\t\t; стек сразу под 0xFFFF — над распаковщиком\n',
        '\t\tld\thl, c_run_src\n',
        '\t\tld\tde, RUN_ADDR\n',
        '\t\tld\tbc, c_run_end - c_run_src\n',
        'c_cp:\t\tld\ta, (hl)\n\t\tld\t(de), a\n\t\tinc\thl\n\t\tinc\tde\n',
        '\t\tdec\tbc\n\t\tld\ta, b\n\t\tor\tc\n\t\tjp\tnz, c_cp\n',
        '\t\tjp\tRUN_ADDR\n',
        'c_run_src:\n',
        '\tDISP\tRUN_ADDR\n',
        '; поток поднимается к верху, копия С КОНЦА: приёмник выше источника\n',
        'c_run:\t\tld\thl, c_stream + STREAM_N - 1\n',
        '\t\tld\tde, STREAM_DST + STREAM_N - 1\n',
        '\t\tld\tbc, STREAM_N\n',
        'c_mv:\t\tld\ta, (hl)\n\t\tld\t(de), a\n\t\tdec\thl\n\t\tdec\tde\n',
        '\t\tdec\tbc\n\t\tld\ta, b\n\t\tor\tc\n\t\tjp\tnz, c_mv\n',
        '\t\tld\thl, STREAM_DST\n',
        '\t\tld\tde, 0x0000\n',
        '\t\tcall\tdz\t\t\t; снимок [0, END) на место\n',
        '\t\tjp\t0x0000\t\t\t; в игру, как после сброса\n',
        decoder_text(),
        '\tENT\n',
        'c_run_end:\n',
        '\t\tASSERT\tRUN_ADDR + (c_run_end - c_run_src) <= 0xFF80\t; стеку остаётся место\n',
        'c_stream:\n',
        '\t\tINCBIN\t"%s"\n' % (os.path.basename(out_path)[:-4] + '.zx0'),
        'c_end:\n',
        '\t\tASSERT\tSTREAM_DST >= c_stream\t; подъём копией с конца: приёмник не ниже источника\n',
        '\t\tASSERT\tc_end <= 0x%04X\t; загруженный образ не заходит в область распаковщика\n' % end,
        '\t\tSAVEBIN\t"%s", 0x%04X, c_end - 0x%04X\n' % (os.path.basename(out_path)[:-4] + '.rom', LOAD, LOAD),
    ]
    open(out_path[:-4] + '.zx0', 'wb').write(stream)
    open(out_path, 'w', encoding='utf-8').write(''.join(src))
    print('%s: поток %d Б (из %d), запас «на месте» %d, образ ~%d Б с 0x%04X'
          % (out_path, n, end, delta, n + 0x200, LOAD))


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    end = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0xF100
    emit(sys.argv[1], sys.argv[2], end)
    return 0


if __name__ == '__main__':
    sys.exit(main())
