#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Звук Knight Lore на AY-3-8910 Вектора-06Ц: перенос драйвера MSX-версии.

Заменяет весь ZX-путь (бипер + очередь ВИ53):
  * мелодии — проигрыватель MSX (0x44FA..0x45E6), но НЕблокирующий: один тик = один
    видеокадр, за кадр отматывается 114 «проходов» MSX (замер SOUND.md §2.4);
  * эффекты — движок MSX (0x4336..0x44F9), тик из объектного цикла, один раз на объект
    (40 объектов = 40 тиков за игровой кадр, ровно как на MSX);
  * данные — src/msx_tunes.bin (490 байт, извлечены из блока A MSX-версии), формат ноты
    тот же, что на ZX; таблица периодов взята как есть (AY Вектора 1.775 МГц против
    1.789772 МГц MSX -> все ноты ниже ровно на 14.35 цента, строй сохраняется).

Порты AY Вектора: out (0x15) — номер регистра, out (0x14) — данные.
Разбор оригинала: ~/projects/knightlore-research/msx/SOUND.md
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
TUNES = os.path.join(HERE, '..', 'src', 'msx_tunes.bin')

# карта msx_tunes.bin (см. msx_tunes_offsets.txt)
O_PERIODS = 0x0000   # 112 байт: периоды нот 8..63
O_CMDTAB = 0x0078    # 16 байт: таблица команд MSX = «периоды» нот 0..7 (пустой канал их пишет)
O_HEADERS = 0x0092   # 36 байт: 6 заголовков по 3 слова
O_INIT = 0x00B6      # 13 байт
O_STOP = 0x00C3      # 9 байт
O_ENV = 0x00CC       # 3 байта
O_STEPS = 0x00CF     # 8 байт
O_STREAMS = 0x00E7   # 259 байт, адреса MSX 0x46C8..0x47CA
MSX_STREAM_BASE = 0x46C8

TUNE_NAMES = ['start_game', 'game_over', 'game_complete', 'menu', 'unused', 'blip']


def _db(name, data, comment=''):
    out = []
    head = name + ':'
    pad = '\t' * max(1, 4 - len(head) // 4)
    for i in range(0, len(data), 16):
        chunk = ', '.join('0x%02X' % b for b in data[i:i + 16])
        if i == 0:
            out.append('%s%sDB\t%s%s' % (head, pad, chunk, ('\t; ' + comment) if comment else ''))
        else:
            out.append('\t\t\t\tDB\t' + chunk)
    return '\n'.join(out) + '\n'


def data_block():
    """Данные драйвера в виде asm-текста (читаются из src/msx_tunes.bin, а не хранятся в коде)."""
    with open(TUNES, 'rb') as f:
        d = f.read()
    assert len(d) == 490, 'msx_tunes.bin: ожидалось 490 байт, получено %d' % len(d)
    periods = d[O_CMDTAB:O_CMDTAB + 16] + d[O_PERIODS:O_PERIODS + 112]   # ноты 0..63, 128 байт
    txt = '\n; ---- данные звука (извлечены из MSX-версии, src/msx_tunes.bin) ----\n'
    txt += _db('msx_per', periods,
               'период по номеру ноты 0..63 (0..7 — «мусор» таблицы команд MSX: пустой канал их пишет с громкостью 0)')
    txt += _db('msx_steptb', d[O_STEPS:O_STEPS + 8], 'ноты эффекта «шаги», индекс = msx_stepc AND 7')
    txt += _db('msx_init', d[O_INIT:O_INIT + 13], 'список PSG перед мелодией')
    txt += _db('msx_stop', d[O_STOP:O_STOP + 9], 'список PSG «полная тишина»')
    txt += _db('msx_env', d[O_ENV:O_ENV + 3], 'R13 := 0 (форма огибающей)')
    txt += _db('msx_kmask', [0x09, 0x12, 0x24], 'биты тона+шума слота 0/1/2 в тени R7')
    # заголовки: 3 слова на мелодию, адреса MSX переносим в наш пул потоков
    for i, nm in enumerate(TUNE_NAMES):
        if nm == 'unused':
            continue                      # мёртвые данные (в MSX на них нет ни одной ссылки)
        w = d[O_HEADERS + 6 * i: O_HEADERS + 6 * i + 6]
        offs = [(w[2 * k] | (w[2 * k + 1] << 8)) - MSX_STREAM_BASE for k in range(3)]
        for o in offs:
            assert 0 <= o < 259, 'заголовок %s: смещение %d вне пула потоков' % (nm, o)
        txt += 'msx_hdr_%s:\tDW\t%s\t; каналы C, B, A\n' % (
            nm, ', '.join('msx_stream+%d' % o for o in offs))
    txt += _db('msx_stream', d[O_STREAMS:O_STREAMS + 259], 'потоки нот всех мелодий (адреса MSX 0x46C8..0x47CA)')
    txt += """
; ---- переменные драйвера ----
msx_mix:		DS		1			; тень регистра 7 (микшер), как 0xD833 на MSX
msx_stepc:		DS		1			; счётчик шагов (индекс в msx_steptb), как 0xD834
msx_si:			DS		1			; текущий слот эффекта 0..2
msx_ci:			DS		1			; текущий канал мелодии 0..2 (0 -> AY C, 1 -> B, 2 -> A)
msx_rem:		DS		1			; сколько «проходов» MSX осталось отмотать в этом кадре
msx_play:		DS		1			; 1 = мелодия играет
msx_key:		DS		1			; 1 = мелодию можно прервать клавишей
msx_end:		DS		1			; 1 = в потоке встретилась команда END
msx_sfx:		DS		12			; 3 слота по 4 байта: id, тиков, параметр_hi, параметр_lo
msx_ch:			DS		21			; 3 канала по 7: ptr(2), dur(2), режим громкости(1), начало потока(2)
"""
    return txt


# ============================================================================
#  Ядро драйвера. Живёт в блоке Вектора (нижний регион), общее для целей vec и z80vec.
#  Пишется в подмножестве i8080: только jp/call/ret, без jr/djnz/ix/exx/bit/ldir.
# ============================================================================
DRIVER = """
; ================= ЗВУК: драйвер MSX-версии на AY Вектора (0x15 адрес, 0x14 данные) =================
; Мелодии: тик = кадр, за кадр отматывается 114 «проходов» главного цикла MSX (SOUND.md §2.4:
;   1024 прохода = 8.98 кадра 50 Гц). Остаток проходов копится в счётчике длительности, поэтому
;   суммарный темп совпадает с MSX без накопления ошибки.
; Эффекты: тик вызывается из объектного цикла по разу на объект — 40 раз за игровой кадр, как на MSX.

msx_wr:			; C = регистр AY, B = значение
				ld		a, c
				out		(0x15), a
				ld		a, b
				out		(0x14), a
				ret

msx_wlist:		; HL = список пар (регистр, значение), терминатор 0xFF
				ld		a, (hl)
				inc		hl
				cp		0xFF
				ret		z
				ld		c, a
				ld		b, (hl)
				inc		hl
				push	hl
				call	msx_wr
				pop		hl
				jp		msx_wlist

msx_snd_init:	; из vec_boot: слоты пусты, мелодии нет, AY молчит
				xor		a
				ld		(msx_play), a
				ld		(msx_end), a
				ld		(msx_stepc), a
				ld		hl, msx_sfx
				ld		b, 12
msi_l:			ld		(hl), 0
				inc		hl
				dec		b
				jp		nz, msi_l
				ld		a, 0xBF
				ld		(msx_mix), a
msx_silence:	ld		hl, msx_stop
				jp		msx_wlist

; ---------------------------------------------------------------- мелодии
msx_play_tune:	; HL = заголовок мелодии (3 слова), A = 1 «прерывать клавишей»
				ld		(msx_key), a
				xor		a
				ld		(msx_end), a
				ld		(msx_play), a		; на время заливки — «не играет» (ISR не влезет)
				push	hl
				ld		hl, msx_init
				call	msx_wlist
				pop		hl
				ld		de, msx_ch
				ld		b, 3
mpt_l:			ld		c, (hl)
				inc		hl
				ld		a, (hl)
				inc		hl
				push	hl
				ld		h, a
				ld		l, c				; HL = начало потока канала
				ld		a, l
				ld		(de), a				; +0 ptr_lo
				inc		de
				ld		a, h
				ld		(de), a				; +1 ptr_hi
				inc		de
				xor		a
				ld		(de), a				; +2 dur_lo = 0 -> первый же проход делает выборку
				inc		de
				ld		(de), a				; +3 dur_hi
				inc		de
				ld		(de), a				; +4 режим громкости = 0
				inc		de
				ld		a, l
				ld		(de), a				; +5 начало потока (для LOOP)
				inc		de
				ld		a, h
				ld		(de), a				; +6
				inc		de
				pop		hl
				dec		b
				jp		nz, mpt_l
				ld		a, 1
				ld		(msx_play), a
				ret

msx_stop_tune:	xor		a
				ld		(msx_play), a
				ld		(msx_end), a
				jp		msx_silence

msx_mtick:		; из ISR раз в кадр
				ld		a, (msx_play)
				or		a
				ret		z
				xor		a
				ld		(msx_ci), a
				ld		hl, msx_ch
				ld		b, 3
mt_ch:			push	bc
				push	hl
				call	msx_chan
				pop		hl
				pop		bc
				ld		a, (msx_end)
				or		a
				jp		nz, mt_done			; END: остальные каналы в этом проходе не обслуживаются
				ld		de, 7
				add		hl, de
				ld		a, (msx_ci)
				inc		a
				ld		(msx_ci), a
				dec		b
				jp		nz, mt_ch
				ret
mt_done:		xor		a					; список остановки уже записал обработчик END
				ld		(msx_play), a
				ld		(msx_end), a
				ret

msx_chan:		; HL -> структура канала, (msx_ci) = индекс. Отмотать 114 проходов MSX.
				ld		a, 114
				ld		(msx_rem), a
mc_loop:		ld		d, h
				ld		e, l
				inc		de
				inc		de					; DE -> dur_lo
				ld		a, (de)
				ld		b, a				; B = dur_lo
				inc		de
				ld		a, (de)				; A = dur_hi
				dec		de
				ld		c, a				; C = dur_hi
				or		b
				jp		z, mc_fetch
				ld		a, c
				or		a
				jp		nz, mc_big			; длительность >= 256 > остатка проходов
				ld		a, (msx_rem)
				cp		b
				jp		c, mc_part			; остаток меньше длительности
				sub		b					; остаток >= длительности: dur -> 0, проходы тратим
				ld		(msx_rem), a
				xor		a
				ld		(de), a				; dur_lo = 0 (dur_hi уже 0)
				ld		a, (msx_rem)
				or		a
				ret		z
				jp		mc_loop
mc_part:		ld		c, b				; C = dur_lo
				ld		a, (msx_rem)
				ld		b, a
				ld		a, c
				sub		b
				ld		(de), a				; dur_lo -= остаток
				xor		a
				ld		(msx_rem), a
				ret
mc_big:			ld		a, (msx_rem)
				ld		b, a
				ld		a, (de)
				sub		b
				ld		(de), a
				jp		nc, mc_big2
				inc		de
				ld		a, (de)
				dec		a
				ld		(de), a
mc_big2:		xor		a
				ld		(msx_rem), a
				ret
mc_fetch:		push	hl
				call	msx_fetch
				pop		hl
				ld		a, (msx_end)
				or		a
				ret		nz
				ld		a, (msx_rem)		; выборка тоже занимает один проход
				dec		a
				ld		(msx_rem), a
				ret		z
				jp		mc_loop

msx_fetch:		; HL -> структура канала: читать события, пока не установится длительность
mf_loop:		ld		e, (hl)
				inc		hl
				ld		d, (hl)
				dec		hl					; DE = указатель потока
				ld		a, (de)
				inc		de
				ld		(hl), e
				inc		hl
				ld		(hl), d
				dec		hl					; указатель сдвинут
				cp		8
				jp		nc, mf_note
				or		a
				jp		z, mf_mode
				cp		4
				jp		z, mf_rest
				cp		6
				jp		z, mf_loopc
				cp		7
				jp		z, mf_end
mf_mode:		; команды 0,1,2,3,5 — только меняют режим громкости
				push	hl
				ld		de, 4
				add		hl, de
				ld		(hl), a
				pop		hl
				jp		mf_loop
mf_loopc:		; команда 6: вернуться на начало потока
				push	hl
				ld		de, 5
				add		hl, de
				ld		e, (hl)
				inc		hl
				ld		d, (hl)
				pop		hl
				ld		(hl), e
				inc		hl
				ld		(hl), d
				dec		hl
				jp		mf_loop
mf_end:			; команда 7: полная тишина и выход из проигрывателя
				ld		a, 1
				ld		(msx_end), a
				jp		msx_silence
mf_rest:		; команда 4: режим 4, громкость по режиму, затем явный ноль, пауза 1024 прохода
				push	hl
				inc		hl
				inc		hl
				ld		(hl), 0				; dur_lo
				inc		hl
				ld		(hl), 4				; dur_hi -> 0x0400 = 1024 прохода
				inc		hl
				ld		(hl), 4				; режим := 4
				pop		hl
				push	hl
				call	msx_volmode
				pop		hl
				xor		a
				jp		msx_setvolm			; поверх режима — явный ноль (как 0x4667)
mf_note:		; A = байт ноты: биты 0-5 номер, 6-7 множитель длительности
				ld		b, a
				push	hl					; указатель структуры канала
				rlca
				rlca
				and		3
				inc		a
				rlca
				rlca						; (множитель+1)*4 = старший байт длительности (1024..4096)
				inc		hl
				inc		hl
				ld		(hl), 0				; dur_lo
				inc		hl
				ld		(hl), a				; dur_hi
				ld		a, b
				and		0x3F
				ld		l, a
				ld		h, 0
				add		hl, hl
				ld		de, msx_per
				add		hl, de
				ld		e, (hl)
				inc		hl
				ld		d, (hl)				; DE = период
				ld		a, (msx_ci)
				add		a, a
				ld		c, a
				ld		a, 4
				sub		c
				ld		c, a				; регистр тона: канал 0 -> R4, 1 -> R2, 2 -> R0
				call	msx_wper
				pop		hl
				jp		msx_volmode

msx_wper:		; C = младший регистр тона, DE = период
				ld		b, e
				push	de
				push	bc
				call	msx_wr
				pop		bc
				pop		de
				inc		c
				ld		b, d
				jp		msx_wr

msx_volmode:	; HL -> структура канала: применить режим громкости (+4)
				push	hl
				inc		hl
				inc		hl
				inc		hl
				inc		hl
				ld		a, (hl)
				pop		hl
				or		a
				jp		z, msx_setvolm		; режим 0 -> 0
				cp		1
				jp		z, mvm_9
				cp		2
				jp		z, mvm_f
				cp		5
				jp		nc, msx_setvolm		; режим 5 в данных не встречается — считаем молчанием
				ld		a, 0x10				; режимы 3 и 4 — огибающая
				call	msx_setvolm
				ld		hl, msx_env
				jp		msx_wlist
mvm_9:			ld		a, 9
				jp		msx_setvolm
mvm_f:			ld		a, 0x0F
msx_setvolm:	; A = значение -> регистр громкости канала мелодии (0 -> R10, 1 -> R9, 2 -> R8)
				ld		b, a
				ld		a, (msx_ci)
				ld		c, a
				ld		a, 10
				sub		c
				ld		c, a
				jp		msx_wr

; ---------------------------------------------------------------- эффекты
msx_sfx_tick:	; из объектного цикла: один раз на объект (40 раз за игровой кадр, как на MSX)
				ld		a, (msx_sfx)
				or		a
				jp		z, mst_1
				xor		a
				ld		(msx_si), a
				call	msx_sfx_run
mst_1:			ld		a, (msx_sfx+4)
				or		a
				jp		z, mst_2
				ld		a, 1
				ld		(msx_si), a
				call	msx_sfx_run
mst_2:			ld		a, (msx_sfx+8)
				or		a
				ret		z
				ld		a, 2
				ld		(msx_si), a
msx_sfx_run:	; (msx_si) = слот, слот занят
				ld		a, (msx_si)
				add		a, a
				add		a, a
				ld		l, a
				ld		h, 0
				ld		de, msx_sfx
				add		hl, de				; HL -> слот
				ld		a, (hl)
				cp		1
				jp		z, msr_noise
				push	af
				call	msx_tone_on
				jp		msr_cnt
msr_noise:		push	af
				call	msx_noise_on
msr_cnt:		inc		hl
				ld		a, (hl)
				dec		a
				ld		(hl), a
				dec		hl
				jp		nz, msr_go
				pop		af
				jp		msx_sfx_kill
msr_go:			ld		b, a				; B = счётчик после декремента
				pop		af					; A = id эффекта
				cp		1
				jp		z, mse1
				cp		2
				jp		z, mse2
				cp		3
				jp		z, mse3
				cp		4
				jp		z, mse4
				cp		5
				jp		z, mse5
				cp		6
				jp		z, mse6
				cp		7
				jp		z, mse7
				ret

mse1:			; шум с затуханием: громкость c/2, период шума растёт
				ld		a, b
				or		a
				rra
				push	bc
				call	msx_setvol
				pop		bc
				ld		a, b
				cpl
				and		0x1F
				ld		b, a
				ld		c, 6
				jp		msx_wr
mse2:			; нисходящий чирп 0x11C..0x102
				ld		a, b
				add		a, b
				ld		e, a
				ld		d, 1
				call	msx_setper
				ld		a, 0x0F
				jp		msx_setvol
mse3:			; ШАГИ: громкость от координат игрока, нота — из циклической таблицы
				ld		a, (graphic_objs_tbl+1)
				or		a
				rra
				ld		c, a
				ld		a, (graphic_objs_tbl+2)
				cpl
				inc		a
				or		a
				rra
				add		a, c
				rlca
				rlca
				rlca
				and		7
				add		a, 8
				call	msx_setvol
				ld		a, (msx_stepc)
				and		7
				ld		l, a
				ld		h, 0
				ld		de, msx_steptb
				add		hl, de
				ld		a, (hl)
				ld		l, a
				ld		h, 0
				add		hl, hl
				ld		de, msx_per
				add		hl, de
				ld		e, (hl)
				inc		hl
				ld		d, (hl)
				jp		msx_setper
mse4:			; тон с 8-битным периодом из параметра, громкость затухает
				push	hl
				ld		de, 3
				add		hl, de
				ld		e, (hl)
				ld		d, 0
				push	bc
				call	msx_setper
				pop		bc
				pop		hl
				ld		a, b
				rrca
				and		0x0F
				jp		msx_setvol
mse5:			ld		a, b
				rlca
				rlca
				xor		0xA0
				ld		e, a
				ld		d, 0
				call	msx_setper
				ld		a, 0x0F
				jp		msx_setvol
mse6:			ld		a, b
				rlca
				rlca
				rlca
				rlca
				ld		e, a
				ld		d, 0
				call	msx_setper
				ld		a, 0x0F
				jp		msx_setvol
mse7:			; тон с 16-битным периодом из параметра
				push	hl
				ld		de, 2
				add		hl, de
				ld		d, (hl)
				inc		hl
				ld		e, (hl)
				call	msx_setper
				pop		hl
				ld		a, 0x0F
				jp		msx_setvol

msx_setper:		; DE = период -> R(2s), R(2s+1)
				ld		a, (msx_si)
				add		a, a
				ld		c, a
				jp		msx_wper
msx_setvol:		; A = значение -> R(8+s)
				ld		b, a
				ld		a, (msx_si)
				add		a, 8
				ld		c, a
				jp		msx_wr

msx_tone_on:	; включить тон канала слота в тени R7 и записать R7 (HL сохраняется)
				push	hl
				call	msx_slotbit
				cpl
				jp		msx_mixand
msx_noise_on:	push	hl
				call	msx_slotbit
				rlca
				rlca
				rlca
				and		0x38
				cpl
msx_mixand:		ld		c, a
				ld		a, (msx_mix)
				and		c
				ld		(msx_mix), a
				ld		b, a
				ld		c, 7
				call	msx_wr
				pop		hl
				ret
msx_slotbit:	; A = 1/2/4 по номеру слота (как 0x43CF: A' = слот+1, тройка заменяется четвёркой)
				ld		a, (msx_si)
				inc		a
				cp		3
				ret		nz
				ld		a, 4
				ret

msx_sfx_kill:	; HL -> слот: освободить, погасить громкость, поднять биты в тени (R7 НЕ пишется — как в оригинале)
				ld		(hl), 0
				xor		a
				call	msx_setvol
				ld		a, (msx_si)
				ld		l, a
				ld		h, 0
				ld		de, msx_kmask
				add		hl, de
				ld		a, (hl)
				ld		c, a
				ld		a, (msx_mix)
				or		c
				ld		(msx_mix), a
				ret

msx_sfx_start:	; A = id, B = тиков, DE = параметр (hi:lo), C = слот
				push	af
				ld		a, (msx_play)
				or		a
				jp		nz, mss_no			; пока играет мелодия, эффектов нет (на MSX игра стоит)
				ld		a, c
				add		a, a
				add		a, a
				ld		l, a
				ld		h, 0
				push	de
				ld		de, msx_sfx
				add		hl, de
				pop		de
				pop		af
				ld		(hl), a
				inc		hl
				ld		(hl), b
				inc		hl
				ld		(hl), d
				inc		hl
				ld		(hl), e
				ret
mss_no:			pop		af
				ret

msx_frame_off:	; конец игрового кадра: полное глушение AY (MSX 0x4136). Мелодию не трогаем.
				ld		a, (msx_play)
				or		a
				ret		nz
				jp		msx_silence
"""

# ---------------------------------------------------------------------------
#  Точки входа, которые видит игровой код (имена ZX сохранены — сайты вызова не трогаем)
# ---------------------------------------------------------------------------
GAME_HOOKS = """
; ---- звук из игрового кода: имена ZX сохранены, внутри — эффекты и мелодии MSX ----
; Соответствие установлено по вызывателям (SOUND.md §3.6) и по формулам параметров:
; MSX-эффект 7 берёт период из ~((ix+1)+(ix+2)+(ix+3)) — это ZX audio_B467;
; MSX-эффект 4 — из ~(ix+3), это ZX audio_B451; MSX-эффект 3 (шаги) считает громкость
; из координат игрока ровно как ZX loc_B4D1.

audio_B3E9:		; ZX «death sparkles» -> MSX эффект 1 (шум с затуханием, слот 0/канал A)
				ld		a, 1
				ld		b, 31
				ld		c, 0
				ld		de, 0
				jp		msx_sfx_start

audio_B403:		; ZX «player appears sparkles» -> MSX эффект 5 (слот 1/канал B)
				ld		a, 5
				ld		b, 31
				ld		c, 1
				ld		de, 0
				jp		msx_sfx_start

audio_B419:		; ZX «ball bouncing around» -> MSX эффект 6 (слот 2/канал C, пила)
				ld		a, 6
				ld		b, 31
				ld		c, 2
				ld		de, 0
				jp		msx_sfx_start

audio_B42E:		; ZX «jumping» -> MSX эффект 2 (слот 0/канал A, нисходящий чирп)
				ld		a, 2
				ld		b, 15
				ld		c, 0
				ld		de, 0
				jp		msx_sfx_start

audio_B441:		; ZX «dropping block / ball up-down» -> MSX эффект 6
				jp		audio_B419

audio_B451:		; ZX «rising blocks»: период из Z объекта -> MSX эффект 4 (слот 1/канал B)
				ld		a, (ix+3)
				cpl
				ld		e, a
				ld		d, 0
				ld		a, 4
				ld		b, 31
				ld		c, 1
				jp		msx_sfx_start

audio_B454:		; хвост ZX (A = координата): на MSX отдельного эффекта нет — берём эффект 4
				cpl
				ld		e, a
				ld		d, 0
				ld		a, 4
				ld		b, 31
				ld		c, 1
				jp		msx_sfx_start

audio_B45D:		; ZX «block/fire moving (EW)»: X объекта
				ld		a, (ix+1)
				jp		audio_B454

audio_B462:		; ZX «fire/block moving (NS)»: Y объекта
				ld		a, (ix+2)
				jp		audio_B454

audio_B467:		; ZX «special objects, ghost, portcullis (up)» -> MSX эффект 7 (слот 1/канал B)
				ld		a, (ix+1)
				add		a, (ix+2)
				add		a, (ix+3)
				cpl
				ld		e, a
				ld		d, 0
				ld		a, 7
				ld		b, 7
				ld		c, 1
				jp		msx_sfx_start

audio_B472:		; ZX «human/wulf transform» -> MSX эффект 5 (слот 1/канал B)
				jp		audio_B403

audio_B489:		; ZX «portcullis» -> MSX эффект 1 (шум)
				jp		audio_B3E9

toggle_audio_hw_x16:	; ZX короткий писк (подбор/сброс предмета, upd_103) -> MSX эффект 2
				jp		audio_B42E

toggle_audio_hw_x24:	; ZX писк паузы/смены пункта меню -> MSX мелодия blip (0x46C2)
				ld		hl, msx_hdr_blip
				xor		a
				jp		msx_play_tune

audio_guard_wizard:		; ZX шаги стража/колдуна: только по чётному кадру анимации
				ld		a, (ix+0)
				and		1
				ret		nz
audio_B4C1:		; ZX шаги -> MSX эффект 3 (слот 2/канал C, таблица из 8 нот + счётчик шагов)
				ld		hl, msx_stepc
				inc		(hl)
				ld		a, 3
				ld		b, 23
				ld		c, 2
				ld		de, 0
				jp		msx_sfx_start
audio_B4BB:		ld		a, (ix+0)
				and		1
				ret		nz
				jp		audio_B4C1
"""

TUNE_PLAYERS = """play_audio_until_keypress:	; DE = заголовок мелодии MSX; как на ZX — опрос клавиши между нотами
				xor		a
				call	read_port
				ret		nz					; клавиша уже нажата — не играем (поведение ZX)
				ld		h, d
				ld		l, e
				ld		a, 1
				call	msx_play_tune
pauk_l:			xor		a
				call	read_port
				jp		nz, msx_stop_tune	; клавиша: оборвать мелодию
				ld		a, (msx_play)
				or		a
				jp		nz, pauk_l			; ISR ещё играет
				ret							; конец мелодии — вернуться
; End of function play_audio_until_keypress
"""

PLAY_AUDIO = """play_audio:		; DE = заголовок мелодии MSX. Не ждём: мелодию доиграет ISR.
				ld		h, d
				ld		l, e
				xor		a
				jp		msx_play_tune
; End of function play_audio
"""


def apply(src):
    """Вырезать ZX-звук и вставить драйвер MSX. Возвращает (src, отчёт)."""
    rep = {}

    def cut(pattern, name, repl='', flags=re.S):
        nonlocal src
        m = re.search(pattern, src, flags)
        assert m, 'kl_sound_ay: не найдено — ' + name
        rep[name] = len(m.group(0))
        src = src[:m.start()] + repl + src[m.end():]

    # 1. таблица частот бипера (183 байта) и ZX-мелодии — больше не нужны;
    #    имена мелодий становятся адресами заголовков MSX
    cut(r'; Frequency Table\?\nfreq_tbl:.*?(?=;\n; Audio Tunes\n;\n)', 'freq_tbl')
    cut(r';\n; Audio Tunes\n;\nstart_game_tune:.*?(?=\n\ngame_over:)',
        'zx_tunes',
        ';\n; Мелодии: данные из MSX-версии, имена ZX = адреса заголовков\n;\n'
        'start_game_tune:\t\tEQU\tmsx_hdr_start_game\n'
        'game_over_tune:\t\t\tEQU\tmsx_hdr_game_over\n'
        'game_complete_tune:\t\tEQU\tmsx_hdr_game_complete\n'
        'menu_tune:\t\t\t\tEQU\tmsx_hdr_menu\n')

    # 2. бипер ZX: toggle_audio_hw и play_note/snd_delay
    cut(r'toggle_audio_hw:\n.*?; End of function toggle_audio_hw\r?\n', 'toggle_audio_hw')
    cut(r'play_note:\n.*?; End of function play_note\r?\n', 'play_note')

    # 3. блок процедур audio_B* целиком -> точки входа на движок MSX
    cut(r'audio_B3E9:\n.*?(?=\n\n; returns C flag set if any other object)', 'audio_block', GAME_HOOKS)

    # 4. проигрыватели мелодий
    cut(r'play_audio_until_keypress:\n.*?; End of function play_audio_until_keypress\r?\n',
        'play_audio_until_keypress', TUNE_PLAYERS)
    cut(r'play_audio:\n.*?; End of function play_audio\r?\n', 'play_audio', PLAY_AUDIO)

    # 5. тик эффектов — в объектный цикл, по одному разу на объект (40 объектов = 40 тиков,
    #    ровно как MSX 0x40FC); вставляем после смены стека, чтобы call лёг на известный стек
    old = "update_sprite_loop:                                             ; init stack\n\t\t\t\tld      sp, seed_1\n"
    assert old in src, 'kl_sound_ay: update_sprite_loop'
    src = src.replace(old, old + "\t\t\t\tcall    msx_sfx_tick                            ; тик эффектов: 1 на объект\n", 1)

    # 6. конец игрового кадра — глушение AY (MSX 0x4136)
    old = "loc_B000:\n\t\t\t\tld      hl, (seed_2)\n"
    assert old in src, 'kl_sound_ay: loc_B000'
    src = src.replace(old, "loc_B000:\n\t\t\t\tcall    msx_frame_off                           ; глушение AY в конце игрового кадра\n\t\t\t\tld      hl, (seed_2)\n", 1)

    return src, rep
