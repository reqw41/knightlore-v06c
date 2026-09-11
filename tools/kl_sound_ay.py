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

Звукового чипа AY на Векторе может не быть, поэтому регистры AY никуда не выводятся:
`msx_wr` пишет в ЗЕРКАЛО из 16 байт (msx_ayregs), а раз в кадр из ISR процедура
`msx_viflush` переносит зеркало в штатный таймер КР580ВИ53 (управление порт 0x08,
счётчики 0/1/2 = порты 0x0B/0x0A/0x09). Раскладка счётчиков и потери — docs/PORT_RECIPE.md §16.
Разбор оригинала: ~/projects/knightlore-research/msx/SOUND.md
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
TUNES = os.path.join(HERE, '..', 'src', 'msx_tunes.bin')
# Шесть звуков, которых на MSX нет вовсе (см. §11) — их источник только бипер ZX. Таблицы периодов
# выведены из кода бипера: один период toggle_audio_hw_xC = 26*B + 82 T на 3.5 МГц, период тона AY
# Вектора (1.775 МГц, деление на 16) = T * 1775000/(16*3500000) = T / 31.5493.
#   src/zx_sfx.bin = 32 байта kl_tbchat (задержки ПЗУ ZX 0x1234..0x1253, читает audio_B403)
#                  + 8 байт kl_tb3e9   (таблица byte_B3FB, читает audio_B3E9)
# Пересчитать: P=lambda B: round((26*B+82)/31.54930); bytes([P(b) for b in rom48[0x1234:0x1254]]
#              + [P(b) for b in (0xA0,0xB0,0xC0,0x90,0xA0,0xE0,0x80,0x60)])
ZXSFX = os.path.join(HERE, '..', 'src', 'zx_sfx.bin')

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
    with open(ZXSFX, 'rb') as f:
        z = f.read()
    assert len(z) == 40, 'zx_sfx.bin: ожидалось 40 байт, получено %d' % len(z)
    txt += _db('kl_tbchat', z[:32], 'треск (ZX audio_B403/B489): период тона по байту задержки бипера, ПЗУ ZX 0x1234')
    txt += _db('kl_tb3e9', z[32:], 'толкаемый блок (ZX audio_B3E9): период тона по таблице задержек byte_B3FB')
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
msx_end:		DS		1			; 1 = в потоке встретилась команда END
msx_sfx:		DS		12			; 3 слота по 4 байта: id, тиков, параметр_hi, параметр_lo
msx_ch:			DS		21			; 3 канала по 7: ptr(2), dur(2), режим громкости(1), начало потока(2)

; ---- выбор источника звука и переменные ВИ53 ----
msx_vact:		DS		3			; голос канала AY активен в этом кадре
msx_vdiv:		DS		6			; делитель ВИ53 для канала AY (lo, hi)
msx_viw:		DS		3			; кому какой счётчик в этом кадре (0xFF = молчит)
msx_viown:		DS		3			; кто занимает счётчик сейчас (0xFF = счётчик остановлен)
msx_vidiv:		DS		6			; кэш делителей счётчиков 0/1/2 — управляющее слово перезапускает счёт
msx_vic0:		DS		1			; 1 = счётчик 0 отдан музыке (ISR не перезаряжает секундомер бюджета di)
msx_vistub:		DS		3			; «out (порт), a : ret» в ОЗУ — у i8080 нет OUT по регистру
				ALIGN	16
msx_ayregs:		DS		16			; зеркало регистров AY: выровнено на 16, msx_wr складывает номер с младшим байтом
"""
    return txt


# ============================================================================
#  Ядро драйвера. Живёт в блоке Вектора (нижний регион), общее для целей vec и z80vec.
#  Пишется в подмножестве i8080: только jp/call/ret, без jr/djnz/ix/exx/bit/ldir.
# ============================================================================
DRIVER = """
; ================= ЗВУК: драйвер MSX-версии на зеркале AY + вывод в ВИ53 =================
; Мелодии: тик = кадр, за кадр отматывается 114 «проходов» главного цикла MSX (SOUND.md §2.4:
;   1024 прохода = 8.98 кадра 50 Гц). Остаток проходов копится в счётчике длительности, поэтому
;   суммарный темп совпадает с MSX без накопления ошибки.
; Эффекты: тик вызывается из объектного цикла по разу на объект — 40 раз за игровой кадр, как на MSX.
; Весь драйвер MSX работает НЕ ТРОНУТЫМ и пишет в зеркало регистров AY; звучит только то,
; что раз в кадр перенесла в ВИ53 процедура msx_viflush (см. блок «ВИ53» ниже).

msx_wr:			; C = регистр AY, B = значение. Источник звука только один — ВИ53, ветка AY снята.
				jp		mwr_vi
				ld		a, c				; режим 0: прямо в AY (0x15 — номер регистра, 0x14 — данные)
				out		(0x15), a
				ld		a, b
				out		(0x14), a
				ret
mwr_vi:			; режим 1: только в зеркало; звучит ВИ53, куда зеркало сливает ISR (msx_viflush)
				push	hl				; HL/DE портить нельзя — сайты вызова писались под OUT-версию
				ld		a, c
				and		0x0F
				add		a, low msx_ayregs	; зеркало выровнено на 16 -> переноса в старший байт нет
				ld		l, a
				ld		h, high msx_ayregs
				ld		(hl), b
				pop		hl
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

; ================= ВИ53: перенос зеркала регистров AY в таймер (раз в кадр из ISR) =================
; Порты Вектора ПЕРЕСТАВЛЕНЫ против даташита 8253: управление 0x08, счётчики 0/1/2 = 0x0B/0x0A/0x09.
; Такт таймера 1497600 Гц, AY Вектора 1.775 МГц с делением на 16 -> делитель = период_AY * 13.5.
; Раскладка счётчиков:
;   мелодия (msx_play=1) — игровой цикл стоит, vec_di_budget не зовут: все три счётчика под музыку,
;     канал AY A/B/C -> счётчик 0/1/2;
;   игра (msx_play=0) — счётчик 0 держит секундомер бюджета di, эффектам остаются 1 и 2:
;     канал B -> счётчик 1, канал C -> счётчик 2, канал A (слот 0 эффектов) подхватывает тот из
;     них, что в этом кадре молчит; если заняты оба — третий голос отбрасывается.
; Три приёма из King's Valley, без которых звук плохой: управляющее слово ПЕРЕЗАПУСКАЕТ счётчик,
; поэтому шлём его только при смене владельца; тишина = управляющее слово БЕЗ делителя (счётчик
; встаёт); при возврате счётчика кэш делителя бьём в 0xFFFF, иначе повтор той же ноты после паузы
; не перезальётся и счётчик промолчит.
; Что теряется против AY: громкость (порог 6 из 15, тише = молчание), огибающая (нота звучит
; полной длины на максимуме), шум (заменён падающим тоном из периода шума R6).

msx_viflush:	; из ISR раз в кадр, ПОСЛЕ msx_mtick
				ld		a, (msx_play)
				ld		hl, msx_vic0
				cp		(hl)
				jp		z, mvf_go
				ld		(hl), a				; смена раскладки счётчиков
				or		a
				jp		nz, mvf_go			; в мелодию: счётчик 0 заберёт первая же нота
				ld		a, 0x30				; обратно в игру: счётчик 0 снова секундомер бюджета di
				out		(0x08), a
				ld		a, 0xFF
				out		(0x0B), a
				out		(0x0B), a
				ld		(msx_viown+0), a
mvf_go:			ld		c, 0				; 1. три голоса из зеркала: активность + делитель
mvf_v:			push	bc
				call	msx_vcalc
				pop		bc
				inc		c
				ld		a, c
				cp		3
				jp		c, mvf_v
				call	msx_viwant			; 2. кому какой счётчик
				ld		a, (msx_vic0)		; 3. выдать (в игре счётчик 0 не трогаем — он секундомер)
				or		a
				ld		c, 1
				jp		z, mvf_e
				ld		c, 0
mvf_e:			push	bc
				call	msx_viemit
				pop		bc
				inc		c
				ld		a, c
				cp		3
				jp		c, mvf_e
				ret

msx_vcalc:		; C = канал AY 0..2 -> msx_vact[C] (активен?), msx_vdiv[2C] (делитель ВИ53)
				ld		b, 0
				ld		hl, msx_vdiv
				add		hl, bc
				add		hl, bc
				push	hl					; -> ячейка делителя
				ld		hl, msx_vact
				add		hl, bc
				push	hl					; -> флаг активности
				ld		a, c
				add		a, 8				; регистр громкости 8+C
				ld		e, a
				ld		d, 0
				ld		hl, msx_ayregs
				add		hl, de
				ld		a, (hl)
				and		0x10				; огибающая включена -> считаем громкость максимальной
				jp		nz, mvc_lo1
				ld		a, (hl)
				and		0x0F
				cp		6					; порог KV: у таймера громкости нет, тихие хвосты гудели бы наравне
				jp		c, mvc_off
mvc_lo1:		ld		hl, msx_kmask		; 0x09/0x12/0x24 = биты тона и шума канала в R7
				add		hl, bc
				ld		e, (hl)
				ld		a, (msx_ayregs+7)
				cpl							; в R7 ноль = включено; после cpl единица = включено
				and		e
				jp		z, mvc_off			; ни тона, ни шума
				and		0x07
				jp		nz, mvc_tone
				ld		a, (msx_ayregs+6)	; только шум: у ВИ53 шума нет — падающий тон из его периода
				and		0x1F
				jp		nz, mvc_nz1
				inc		a
mvc_nz1:		ld		h, a				; делитель = R6*256: R6 = 1..31 -> 5850..188 Гц
				ld		l, 0
				jp		mvc_on
mvc_tone:		ld		a, c
				add		a, a
				ld		e, a
				ld		d, 0
				ld		hl, msx_ayregs
				add		hl, de
				ld		a, (hl)
				inc		hl
				ld		h, (hl)
				ld		l, a				; HL = период AY
				call	msx_ay2vi			; HL = период * 13.5
				ld		a, h
				or		a
				jp		nz, mvc_on
				ld		a, l
				cp		64					; делитель < 64 = выше 23 кГц: эффекты дают период 0..4, слышно всё равно нечего
				jp		c, mvc_off
mvc_on:			pop		de
				ld		a, 1
				ld		(de), a
				pop		de
				ld		a, l
				ld		(de), a
				inc		de
				ld		a, h
				ld		(de), a
				ret
mvc_off:		pop		hl
				ld		(hl), 0
				pop		hl
				ret

msx_ay2vi:		; HL = период AY (12 бит) -> HL = делитель ВИ53 (13P + P/2)
				ld		a, h
				and		0x0F
				ld		h, a
				ld		b, h
				ld		c, l				; BC = P
				add		hl, hl
				ld		e, l
				ld		d, h				; DE = 2P
				add		hl, hl				; 4P
				add		hl, de				; 6P
				add		hl, hl				; 12P
				add		hl, bc				; 13P
				ld		a, b
				or		a
				rra
				ld		b, a
				ld		a, c
				rra
				ld		c, a				; BC = P/2
				add		hl, bc				; 13.5P
				ret

msx_viwant:		; msx_viw[счётчик] = номер канала AY или 0xFF (счётчик молчит)
				ld		c, 0				; по умолчанию канал n -> счётчик n
mvw_l:			ld		b, 0
				ld		hl, msx_vact
				add		hl, bc
				ld		a, (hl)
				or		a
				ld		a, c
				jp		nz, mvw_s
				ld		a, 0xFF
mvw_s:			ld		hl, msx_viw
				add		hl, bc
				ld		(hl), a
				inc		c
				ld		a, c
				cp		3
				jp		c, mvw_l
				ld		a, (msx_vic0)
				or		a
				ret		nz					; мелодия: три счётчика на три канала, перекладывать нечего
				ld		a, 0xFF				; игра: счётчик 0 держит секундомер бюджета di
				ld		(msx_viw+0), a
				ld		a, (msx_vact+0)		; канал A (слот 0: чирп шара, приземление) ищет свободный счётчик
				or		a
				ret		z
				ld		a, (msx_viw+2)		; сперва счётчик 2: шаги обычно там же, но если C молчит — он свободен
				inc		a
				jp		nz, mvw_g3
				xor		a
				ld		(msx_viw+2), a
				ret
mvw_g3:			ld		a, (msx_viw+1)
				inc		a
				ret		nz					; оба счётчика заняты — третий одновременный эффект отброшен
				xor		a
				ld		(msx_viw+1), a
				ret

msx_viemit:		; C = номер счётчика 0..2
				ld		a, 0x0B
				sub		c
				ld		(msx_vistub+1), a	; порт счётчика: 0x0B / 0x0A / 0x09
				ld		b, 0
				ld		hl, msx_viw
				add		hl, bc
				ld		a, (hl)
				ld		hl, msx_viown
				add		hl, bc
				inc		a
				jp		z, mve_off			; 0xFF -> счётчику молчать
				dec		a
				cp		(hl)
				jp		z, mve_div			; владелец тот же — управляющее слово не слать
				ld		(hl), a
				push	af
				call	mve_ctrl			; смена владельца: перезапуск счётчика...
				ld		hl, msx_vidiv
				add		hl, bc
				add		hl, bc
				ld		(hl), 0xFF			; ...и бой кэша делителя (иначе та же нота после паузы промолчит)
				inc		hl
				ld		(hl), 0xFF
				pop		af
mve_div:		ld		l, a
				ld		h, 0
				add		hl, hl				; 2*владелец
				ld		de, msx_vdiv
				add		hl, de
				ld		e, (hl)
				inc		hl
				ld		d, (hl)				; DE = делитель
				ld		hl, msx_vidiv
				add		hl, bc
				add		hl, bc
				ld		a, (hl)
				cp		e
				jp		nz, mve_w
				inc		hl
				ld		a, (hl)
				cp		d
				ret		z					; делитель тот же — не трогаем (запись перезапустила бы счётчик)
				dec		hl
mve_w:			ld		(hl), e
				inc		hl
				ld		(hl), d
				ld		a, e
				call	msx_vistub
				ld		a, d
				jp		msx_vistub
mve_off:		ld		a, (hl)
				inc		a
				ret		z					; уже молчит
				ld		(hl), 0xFF
mve_ctrl:		ld		a, c				; управляющее слово «режим 3, LSB+MSB» счётчика C
				rrca
				rrca						; 0 -> 0x00, 1 -> 0x40, 2 -> 0x80
				add		a, 0x36
				out		(0x08), a
				ret

msx_snd_init:	; из vec_boot: слоты пусты, мелодии нет, звук молчит
				ld		a, 0xD3				; заготовка «out (порт), a : ret» — порт счётчика ВИ53 правится на лету
				ld		(msx_vistub), a
				ld		a, 0xC9
				ld		(msx_vistub+2), a
				ld		a, 0xFF				; ни один счётчик ВИ53 не занят голосом
				ld		(msx_viown+0), a
				ld		(msx_viown+1), a
				ld		(msx_viown+2), a
				xor		a
				ld		(msx_vic0), a
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
msx_play_tune:	; HL = заголовок мелодии (3 слова). Флага «прерывать клавишей» (MSX 0xD830) нет:
				; клавишу опрашивает обёртка play_audio_until_keypress, а не сам проигрыватель.
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
				cp		8
				jp		z, mse8
				cp		9
				jp		z, mse9
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
mse8:			; ТРЕСК (звук сверх MSX, источник — бипер ZX): период = kl_tbchat[(тик + смещение) AND 31].
				; ZX крутил цикл по байтам ПЗУ и слышал каждый; ВИ53 получает зеркало раз в кадр, поэтому
				; из последовательности слышно по одному значению на кадр — «щелчки» вместо сплошного треска.
				; параметр hi = 1 -> период пополам (ZX маскирует задержку 0x7F, то есть октавой выше)
				inc		hl
				inc		hl
				ld		c, (hl)				; +2: флаг октавы
				inc		hl
				ld		a, (hl)				; +3: смещение в таблице
				add		a, b
				and		0x1F
				ld		e, a
				ld		d, 0
				ld		hl, kl_tbchat
				add		hl, de
				ld		a, (hl)
				ld		e, a
				ld		a, c
				or		a
				jp		z, mse8_1
				ld		a, e
				or		a					; сбросить перенос перед сдвигом
				rra
				ld		e, a
mse8_1:			ld		d, 0
				call	msx_setper
				ld		a, 0x0F
				jp		msx_setvol
mse9:			; РАЗВЁРТКА ВВЕРХ (звук сверх MSX, источник — бипер ZX audio_B419): ZX даёт C периодов
				; с задержкой B = 4C, то есть период 104C + 98 T; на период тона AY это 3.296C + 3.11,
				; считаем как 3C + C/4 + 3 (ошибка <= 2 %, на слух ровно та же развёртка)
				ld		a, b
				rrca
				rrca
				and		0x3F
				ld		c, a				; C = тик/4
				ld		a, b
				add		a, b
				add		a, b
				add		a, c
				add		a, 3
				ld		e, a
				ld		d, 0
				call	msx_setper
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
; ---- звук из игрового кода: имена процедур ZX сохранены, внутри — эффекты и мелодии MSX ----
; Соответствие «вызыватель ZX -> эффект MSX» установлено сверкой кода перед каждым из 22
; вызовов звука в блоке A MSX (blockA_4000.lst) с тем же местом ZX-исходника; три опорных
; совпадения формул: MSX-эффект 7 берёт период из ~((ix+1)+(ix+2)+(ix+3)) — это ZX audio_B467;
; MSX-эффект 4 — из ~(ix+3), это ZX audio_B451; MSX-эффект 3 (шаги) считает громкость из
; координат игрока ровно как ZX loc_B4D1.
; Где у MSX звука нет, стоит ret — список расхождений в docs/PORT_RECIPE.md §11.

; ---- шесть звуков, которых на MSX НЕТ ВОВСЕ: переложены с бипера ZX (docs/PORT_RECIPE.md §20).
; Общая мера: один период toggle_audio_hw_xC = 26*B + 82 T на 3.5 МГц; период тона AY = T / 31.5493.
; Длительность на ZX = число периодов * период; у нас единица — тик эффекта (40 тиков на игровую
; итерацию ~ 50 мс), поэтому «тиков» подобрано так, чтобы совпасть по времени, но не короче кадра:
; ВИ53 получает зеркало раз в кадр, эффект короче кадра рискует не попасть ни под один слив.

audio_B3E9:		; ZX: толкаемый блок (upd_62) — 4 периода, задержка из byte_B3FB по seed_2 AND 7
				; (593..1358 Гц, 3.9 мс). -> готовый эффект 7 (ровный тон), слот 0 / канал A.
				; Растянут до 16 тиков (~20 мс): 4 мс на один слив в кадр не попадают.
				ld		a, (seed_2)
				and		7
				ld		l, a
				ld		h, 0
				ld		de, kl_tb3e9
				add		hl, de
				ld		e, (hl)
				ld		d, 0
				ld		a, 7
				ld		b, 16
				ld		c, 0
				jp		msx_sfx_start

audio_B403:		; ZX: вспышка котла / распад объекта — (~(ix+0)) AND 31 итераций по 2 периода,
				; задержка = байт ПЗУ ZX 0x1234.. (531 Гц..37 кГц, 43 мс при 31 итерации).
				; -> эффект 8 (треск по kl_tbchat), слот 2 / канал C, тиков = столько же, сколько
				; итераций у ZX — длительность совпадает (31 тик ~ 39 мс против 43 мс).
				; D сохраняем: cycle_colours_with_sound держит в нём счётчик 16 вспышек.
				push	de
				ld		a, (ix+0)
				cpl
				and		0x1F
				ld		b, a
				ld		de, 0				; смещение 0 (последовательность ZX), низкая октава
				ld		a, 8
				ld		c, 2
				call	msx_sfx_start
				pop		de
				ret

audio_B419:		; ZX: «искры появления» игрока — C = ((gfx*4) AND 31) OR 3 периодов с задержкой 4C,
				; тон поднимается 1053 -> 17327 Гц за 15.6 мс. -> эффект 9 (развёртка вверх),
				; слот 0 / канал A. Тиков = C: на один слив в кадр 15.6 мс дали бы один щелчок,
				; а на C тиков (~39 мс при C=31) слышно два-три шага развёртки.
				ld		a, (ix+0)
				rlca
				rlca
				and		0x1F
				or		3
				ld		b, a
				ld		de, 0
				ld		a, 9
				ld		c, 0
				jp		msx_sfx_start

audio_B454:		; ZX: покадровая проверка «блоки поднимаются» — 6 периодов, задержка (~rising_blocks_z)*4.
				; -> готовый эффект 4 (тон с 8-битным периодом и затуханием), слот 0 / канал A,
				; период = ~rising_blocks_z — ровно тот закон, которым MSX заменил ZX в audio_B451.
				; 31 тик: вызов идёт раз в игровую итерацию, гул выходит сплошным, как на ZX.
				ld		a, (rising_blocks_z)
				cpl
				ld		e, a
				ld		d, 0
				ld		a, 4
				ld		b, 31
				ld		c, 0
				jp		msx_sfx_start

audio_B489:		; ZX: решётка ударилась о пол — 16 итераций по 2 периода, задержка = байт ПЗУ ZX
				; по СЛУЧАЙНОМУ адресу (seed_2 AND 31):(seed_3), маска 0x7F (>= 1034 Гц, 15.7 мс).
				; -> эффект 8 по той же kl_tbchat, но со случайным смещением (seed_3) и периодом
				; пополам — маска 0x7F у ZX ровно вдвое режет задержку, то есть поднимает октаву.
				ld		a, (seed_3)
				ld		e, a
				ld		d, 1
				ld		a, 8
				ld		b, 16
				ld		c, 1
				jp		msx_sfx_start

toggle_audio_hw_x16:	; ZX: подтверждение подбора предмета и «получена жизнь» — 16 периодов с
				; задержкой 0x80 = ровный тон 1026 Гц, 15.6 мс. -> готовый эффект 7, слот 2 / канал C,
				; период AY 108 = 1027 Гц, 16 тиков ~ 20 мс.
				ld		a, 7
				ld		b, 16
				ld		c, 2
				ld		de, 108
				jp		msx_sfx_start

audio_B42E:		; шар/огонь сменил направление (MSX 0x4A07, 0x4C44, 0x4C9C) -> эффект 2, слот 0 / канал A
				ld		a, 2
				ld		b, 15
				ld		c, 0
				ld		de, 0
				jp		msx_sfx_start

audio_B441:		; прыжок игрока (MSX 0x5D8A) -> эффект 5, слот 1 / канал B
				ld		a, 5
				ld		b, 31
				ld		c, 1
				ld		de, 0
				jp		msx_sfx_start

audio_B472:		; превращение человек/волк (MSX 0x5756) -> эффект 6, слот 2 / канал C
				ld		a, 6
				ld		b, 31
				ld		c, 2
				ld		de, 0
				jp		msx_sfx_start

audio_B451:		; падающий блок/шипастый шар долетел до пола (MSX 0x4A7B, 0x4BE3)
				; -> эффект 4, слот 1 / канал B, период = ~(ix+3)
				ld		a, (ix+3)
				cpl
				ld		e, a
				ld		d, 0
				ld		a, 4
				ld		b, 31
				ld		c, 1
				jp		msx_sfx_start

audio_B467:		; спецобъект/привидение/решётка вверх, огонь и блоки в движении
				; (MSX 0x5632, 0x5A11, 0x5B14, 0x4C0E, 0x4C30, 0x4A93, 0x4AC8, 0x4C86, 0x58C7)
				; -> эффект 7, слот 1 / канал B, период = ~((ix+1)+(ix+2)+(ix+3))
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
audio_B45D:		jp		audio_B467		; ZX брал X объекта, MSX — сумму координат (одна точка входа 0x446F)
audio_B462:		jp		audio_B467		; то же для Y

audio_msx_noise:	; приземление игрока (MSX 0x5A84, прямой вызов 0x4340) -> эффект 1, шум, слот 0 / канал A
				ld		a, 1
				ld		b, 31
				ld		c, 0
				ld		de, 0
				jp		msx_sfx_start

audio_msx_blip:	; смена настройки в меню и пауза (MSX 0x517A, 0x68C6, 0x68DE) -> мелодия blip 0x46C2
				ld		de, msx_hdr_blip
				jp		play_audio
toggle_audio_hw_x24:	jp		audio_msx_blip

audio_guard_wizard:		; шаги охранника/колдуна: только по чётному кадру анимации (звук сверх MSX —
				; там оба вызова эффекта 3 достались игроку, см. §11)
				ld		a, (ix+0)
				and		1
				ret		nz
audio_B4C1:		; шаги игрока: поворот (MSX 0x5D37) -> эффект 3, слот 2 / канал C
				ld		hl, msx_stepc
				inc		(hl)
				ld		a, 3
				ld		b, 23
				ld		c, 2
				ld		de, 0
				jp		msx_sfx_start
audio_B4BB:		; шаг вперёд (MSX 0x5DAB)
				ld		a, (ix+0)
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

PLAY_AUDIO = """play_audio:		; DE = заголовок мелодии MSX. Звук делает ISR, а игра ждёт конца — как в оригинале
				; (на MSX проигрыватель блокирующий, 0x452E). Ждать ОБЯЗАТЕЛЬНО: под `di` в
				; print_sprite Вектор теряет кадровые прерывания (они не защёлкиваются), и
				; играющая поверх игрового цикла мелодия растягивается примерно на 20 %.
				ld		h, d
				ld		l, e
				call	msx_play_tune
pa_l:			ld		a, (msx_play)
				or		a
				jp		nz, pa_l
				ret
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

    # 5. три вызова, которые на MSX ведут не туда, куда общая процедура ZX: разводим адресно
    for old, new, name in (
        ("\t\t\t\tcall    NZ, toggle_audio_hw_x16                 ; yes\n",
         "\t\t\t\tcall    NZ, audio_msx_blip                       ; MSX 0x517A: blip при смене настройки\n",
         'смена настройки в меню -> blip'),
        ("\t\t\t\tcall    M, audio_B451\n",
         "\t\t\t\tcall    M, audio_msx_noise                      ; MSX 0x5A84: шум при приземлении\n",
         'приземление игрока -> эффект 1 (шум)'),
        ("\t\t\t\tcall    toggle_next_prev_sprite\n\t\t\t\tcall    audio_B451\n\t\t\t\tbit     2, (ix+13)                               ; dir=up?\n",
         "\t\t\t\tcall    toggle_next_prev_sprite\n\t\t\t\tcall    audio_B467                              ; MSX 0x4C86: эффект 7, не 4\n\t\t\t\tbit     2, (ix+13)                               ; dir=up?\n",
         'высота отскока шара -> эффект 7'),
    ):
        assert src.count(old) == 1, 'kl_sound_ay: сайт не уникален — ' + name
        src = src.replace(old, new, 1)
        rep['сайт: ' + name] = 0

    # 6. тик эффектов — в объектный цикл, по одному разу на объект (40 объектов = 40 тиков,
    #    ровно как MSX 0x40FC); вставляем после смены стека, чтобы call лёг на известный стек
    old = "update_sprite_loop:                                             ; init stack\n\t\t\t\tld      sp, seed_1\n"
    assert old in src, 'kl_sound_ay: update_sprite_loop'
    src = src.replace(old, old + "\t\t\t\tcall    msx_sfx_tick                            ; тик эффектов: 1 на объект\n", 1)

    # 7. вопроса об источнике звука больше нет: единственный источник — ВИ53

    # 8. конец игрового кадра — глушение AY (MSX 0x4136)
    # 9. cycle_colours_with_sound (вспышка котла) крутит 16 раз «audio_B403 + пауза 0x2000» ВНЕ
    #    объектного цикла, поэтому эффект там не тикал бы ни разу и звук не дошёл бы до зеркала.
    #    Пауза перестроена так, чтобы внутренний цикл остался прежней цены (dec bc / ld a,? / or ? /
    #    jr nz), а тик звука вызывался раз на 256 проходов — 32 тика на вспышку, ровно на один
    #    полный треск (<= 31 тик), как и у ZX: 16 пачек шума с паузами.
    old = ("loc_C2C3:\n\t\t\t\tdec     bc\n\t\t\t\tld      a, b\n\t\t\t\tor      c\n"
           "\t\t\t\tjr      NZ, loc_C2C3\n")
    assert src.count(old) == 1, 'kl_sound_ay: пауза cycle_colours_with_sound'
    src = src.replace(old,
                      "loc_C2C3:\n\t\t\t\tdec     bc\n\t\t\t\tld      a, c\n\t\t\t\tor      a\n"
                      "\t\t\t\tjr      NZ, loc_C2C3\n"
                      "\t\t\t\tpush    bc                                      ; тик звука: bc/de живые\n"
                      "\t\t\t\tpush    de\n"
                      "\t\t\t\tcall    msx_sfx_tick\n"
                      "\t\t\t\tpop     de\n"
                      "\t\t\t\tpop     bc\n"
                      "\t\t\t\tld      a, b\n\t\t\t\tor      a\n\t\t\t\tjr      NZ, loc_C2C3\n", 1)
    rep['пауза вспышки котла тикает звук'] = 0

    old = "loc_B000:\n\t\t\t\tld      hl, (seed_2)\n"
    assert old in src, 'kl_sound_ay: loc_B000'
    src = src.replace(old, "loc_B000:\n\t\t\t\tcall    msx_frame_off                           ; глушение AY в конце игрового кадра\n\t\t\t\tld      hl, (seed_2)\n", 1)

    return src, rep
