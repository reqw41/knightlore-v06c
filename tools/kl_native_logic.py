# -*- coding: utf-8 -*-
"""Нативные i8080-процедуры Knight Lore (logic): применяются к транслируемому тексту после kl_native.apply.
Метод: спецификация на Python (spec/kl_spec_logic.py) -> дамп вызовов (tools/calldump_vec.js) -> asm; см. docs/PORT_RECIPE.md."""
import re

SCRATCH = ""   # ячейки нулевой страницы (DS), добавляются после kl_native.SCRATCH

# ---------------------------------------------------------------------------
# is_near_to: |centre_x-X| < L, |centre_y-Y| < H, |centre_z-Z| < 4 (CY = близко).
# Оригинал портит только A и флаги, BC/DE/HL/IX/IY сохраняет — вызывается из циклов
# по 4 объектам (chk_plyr_spec_near_arch, loc_C791), где HL = пороги, DE = 32, B = счётчик.
# Транслированный вариант: 6 обращений (ix/iy+n) встроенным разворачиванием, 172 Б.
# ---------------------------------------------------------------------------
N_IS_NEAR_TO = """is_near_to:		; CY = объекты (z80_ix) и (z80_iy) рядом; A = последняя разность. BC/DE/HL сохраняются.
				push	bc
				push	de
				push	hl
				ld		b, h								; порог по Y
				ld		c, l								; порог по X
				ld		hl, (z80_ix)
				ld		a, l
				add		a, 9
				ld		l, a
				ld		a, h
				adc		a, 0
				ld		h, a
				ex		de, hl								; DE = ix+9 (centre_x)
				ld		hl, (z80_iy)
				inc		hl									; HL = iy+1 (X)
				ld		a, (de)
				sub		(hl)
				jp		nc, inr_x
				cpl
				inc		a									; neg
inr_x:			cp		c
				jp		nc, inr_out
				inc		de
				inc		hl
				ld		a, (de)
				sub		(hl)
				jp		nc, inr_y
				cpl
				inc		a
inr_y:			cp		b
				jp		nc, inr_out
				inc		de
				inc		hl
				ld		a, (de)
				sub		(hl)
				jp		nc, inr_z
				cpl
				inc		a
inr_z:			cp		4
inr_out:		pop		hl
				pop		de
				pop		bc
				ret
; End of function is_near_to
"""


# ---------------------------------------------------------------------------
# chk_plyr_spec_near_arch: пройти 4 первых объекта (игрок + спецобъекты), и тем из них,
# что не пусты, помечены «auto-adjust» (бит 3 поля 7) и близки к арке, выставить бит 0 поля 7.
# Оригинал держал объект в IY: 4 обращения (iy+n) на объект (~306 тактов/объект).
# Здесь указатель объекта живёт в DE, пороги — в HL (is_near_to их сохраняет),
# z80_iy заполняется только перед вызовом is_near_to.
# Выходные регистры (A/F/B/DE/HL/IY) у оригинала мертвы: вызыватель loc_C785 сразу
# перезадаёт HL, IY, DE, B; C не трогается ни там, ни здесь.
# ---------------------------------------------------------------------------
N_CHK_NEAR_ARCH = """chk_plyr_spec_near_arch:	; HL = пороги для is_near_to; помечает близкие объекты битом 0 поля 7
				ld		de, graphic_objs_tbl
				ld		b, 4								; только игрок и спецобъекты
cpa_1:			ld		a, (de)								; graphic no
				or		a									; null?
				jp		z, cpa_n							; да, дальше
				push	hl									; пороги
				ld		h, d
				ld		l, e
				ld		(z80_iy), hl						; текущий объект для is_near_to
				ld		a, l
				add		a, 7
				ld		l, a								; +7 не переносит: младший байт объекта <= 0xE8
				ld		a, (hl)
				and		8									; auto-adjust?
				pop		hl
				jp		z, cpa_n							; нет, дальше
				call	is_near_to
				jp		nc, cpa_n							; не рядом
				push	hl
				ld		hl, (z80_iy)
				ld		a, l
				add		a, 7
				ld		l, a
				ld		a, (hl)
				or		1									; flag near arch
				ld		(hl), a
				pop		hl
cpa_n:			ld		a, e								; следующий объект
				add		a, 32
				ld		e, a
				ld		a, d
				adc		a, 0
				ld		d, a
				dec		b
				jp		nz, cpa_1
				push	hl
				ld		hl, graphic_objs_tbl+128			; IY после цикла — как у оригинала
				ld		(z80_iy), hl
				pop		hl
				ret
; End of function chk_plyr_spec_near_arch
"""


# ---------------------------------------------------------------------------
# set_draw_objs_overlapped: пролог (calc_2d_info + объединённый прямоугольник) оставлен как был,
# переписан ГОРЯЧИЙ цикл по 40 объектам (78 проходов на итерацию, 2 вызова).
# Что изменилось против версии kl_native:
#   * четыре параметра прямоугольника (левый край, ширина, нижний Y, высота) пролог кладёт
#     НЕПОСРЕДСТВЕННО в иммедиаты sub/cp внутри цикла -> сравнение стоит 7 тактов и не занимает
#     регистров; DE освобождён под постоянный шаг 32 (add hl,de вместо ld de,32 + add hl,de);
#   * поле флагов адресуется как l+7 без adc: младший байт объекта <= (база&31)+224, а
#     (graphic_objs_tbl & 31) = 8, значит +7 никогда не переносит (см. ASSERT);
#   * pixel X/Y читаются одним проходом вперёд (inc hl), возврат к базе — единственный pop.
# Такты на объект: пусто 46 (было 56), уже DRAW 100 (было 128), полная проверка 208-227 (было 283-290).
# ---------------------------------------------------------------------------
N_SET_DRAW = """set_draw_objs_overlapped:	; DRAW всем объектам, попавшим в объединённый прямоугольник старого и нового положения (ix)
				ASSERT (graphic_objs_tbl & 31) <= 24		; иначе l+7 переносит и адресация поля флагов ломается
				call	calc_2d_info
				ld		hl, (z80_ix)
				ld		de, 24
				add		hl, de
				ld		a, (hl)
				ld		(kl_pw), a
				inc		hl
				ld		a, (hl)
				ld		(kl_ph), a
				inc		hl
				ld		a, (hl)
				ld		(kl_px), a
				inc		hl
				ld		a, (hl)
				ld		(kl_py), a
				inc		hl
				ld		a, (hl)
				ld		(kl_opw), a
				inc		hl
				ld		a, (hl)
				ld		(kl_oph), a
				inc		hl
				ld		a, (hl)
				ld		(kl_opx), a
				inc		hl
				ld		a, (hl)
				ld		(kl_opy), a
				ld		a, (kl_px)
				rrca
				rrca
				rrca
				and		0x1F
				ld		c, a								; xb
				ld		a, (kl_opx)
				rrca
				rrca
				rrca
				and		0x1F
				ld		b, a								; oxb
				cp		c
				jp		c, sd_1
				ld		a, c
sd_1:			ld		(sd_ie+1), a						; левый край -> иммедиат
				ld		a, c
				ld		hl, kl_pw
				add		a, (hl)
				ld		c, a								; xb + pw
				ld		a, b
				ld		hl, kl_opw
				add		a, (hl)
				cp		c
				jp		nc, sd_2
				ld		a, c
sd_2:			ld		hl, sd_ie+1
				sub		(hl)
				ld		(sd_id+1), a						; объединённая ширина -> иммедиат
				ld		a, (kl_py)
				ld		hl, kl_opy
				cp		(hl)
				jp		c, sd_3
				ld		a, (hl)
sd_3:			ld		(sd_il+1), a						; нижний Y -> иммедиат
				ld		a, (kl_py)
				ld		hl, kl_ph
				add		a, (hl)
				ld		c, a
				ld		a, (kl_opy)
				ld		hl, kl_oph
				add		a, (hl)
				cp		c
				jp		nc, sd_4
				ld		a, c
sd_4:			ld		hl, sd_il+1
				sub		(hl)
				ld		(sd_ih+1), a						; объединённая высота -> иммедиат
				ld		hl, graphic_objs_tbl
				ld		de, 32								; шаг объекта живёт в DE весь цикл
				ld		b, 40
sd_loop:		ld		a, (hl)								; graphic no
				or		a
				jp		z, sd_next
				ld		a, l
				add		a, 7
				ld		l, a								; -> поле флагов
				ld		a, (hl)
				and		0x10								; уже DRAW?
				jp		nz, sd_n7
				push	hl
				ld		a, l
				add		a, 19
				ld		l, a
				ld		a, h
				adc		a, 0
				ld		h, a								; -> +26 pixel X
				ld		a, (hl)
				rrca
				rrca
				rrca
				and		0x1F								; ax
sd_ie:			sub		0									; - левый край (иммедиат из пролога)
				jp		c, sd_xneg
sd_id:			cp		0									; >= объединённой ширины?
				jp		nc, sd_pn
				jp		sd_yc
sd_xneg:		cpl
				inc		a									; левый край - ax
				dec		hl
				dec		hl									; -> +24 pw объекта
				cp		(hl)
				jp		nc, sd_pn
				inc		hl
				inc		hl
sd_yc:			inc		hl									; -> +27 pixel Y
				ld		a, (hl)
sd_il:			sub		0									; - нижний Y
				jp		c, sd_yneg
sd_ih:			cp		0									; >= объединённой высоты?
				jp		nc, sd_pn
				jp		sd_set
sd_yneg:		cpl
				inc		a
				dec		hl
				dec		hl									; -> +25 ph объекта
				cp		(hl)
				jp		nc, sd_pn
sd_set:			pop		hl									; -> поле флагов
				ld		a, (hl)
				or		0x10
				ld		(hl), a
				jp		sd_n7
sd_pn:			pop		hl
sd_n7:			ld		a, l
				sub		7
				ld		l, a
sd_next:		add		hl, de
				dec		b
				jp		nz, sd_loop
				ld		hl, graphic_objs_tbl+1280			; IY после цикла = конец таблицы (как оригинал)
				ld		(z80_iy), hl
				ret
; End of function set_draw_objs_overlapped
"""


# ---------------------------------------------------------------------------
# Обвязка перебора объектов (40 объектов на итерацию).
#
# ОТВЕРГНУТО: класть адрес возврата диспетчера в seed_1-2 раз за кадр и ставить
#    ld sp,seed_1-2 вместо ld sp,seed_1 + ld hl,ret_from_tbl_jp + push hl (-21 такт
#    на объект, -840 на итерацию). Локстеп зелёный, но цель vec виснет: там живо
#    прерывание RST 38h, а между ret обработчика (SP снова = seed_1) и следующим
#    ld sp прерывание успевает положить af/bc/de/hl поверх слова в seed_1-2.
#    Слово, из которого делается ret, обязано лежать НИЖЕ SP всё время — с ret это
#    невозможно. Оставлен оригинальный push.
#
# ret_from_tbl_jp: вместо 16-битного ix+=32 через HL и 16-битного сравнения с концом
#    таблицы — инкремент младшего байта z80_ix на месте (перенос в старший — 1 раз из 8)
#    и сравнение младшего байта с младшим байтом clear_end; старший проверяется только
#    когда младшие совпали (5 объектов из 40). 107 -> ~62 такта на объект.
# ---------------------------------------------------------------------------
N_RET_FROM_TBL = """ret_from_tbl_jp:	; (ld a,r -> 0) seed_3 += 0; IX += 32; конец таблицы -> loc_B000, иначе следующий объект
				ld		hl, z80_ix
				ld		a, (hl)
				add		a, 32
				ld		(hl), a
				jp		nc, rft_1
				inc		hl
				inc		(hl)								; перенос в старший байт (каждый 8-й объект)
				dec		hl
rft_1:			cp		low clear_end
				jp		nz, update_sprite_loop
				inc		hl
				ld		a, (hl)
				cp		high clear_end
				jp		z, loc_B000
				jp		update_sprite_loop
"""


# ---------------------------------------------------------------------------
# Блок арок: upd_3_5 и upd_2_4 (+ loc_C74C, loc_C760, adj_2_4_hflip) и цикл loc_C785
# по 4 объектам, который выставляет соседям арки dX/dY.
# Все (ix+n) заменены на ход по HL от (z80_ix): смещения 1..11 не переносят младший байт
# (ASSERT в set_draw_objs_overlapped), поэтому адресация идёт add/sub по L.
# В цикле loc_C785 указатель объекта живёт в DE, пороги — в HL.
#
# ПРИЧУДА ОРИГИНАЛА, которую обязательно сохранить: HL входит в is_near_to как пороги,
# но путь adj_ew/adj_ns идёт через lookup_plyr_dXY -> jump_to_tbl_entry, который кладёт
# в HL АДРЕС обработчика. Со следующего объекта «пороги» = байты адреса adj_ew/adj_ns.
# Поэтому adj_ew/adj_ns оставлены исходным текстом (их (ix/iy+n) сохраняют HL сами),
# а loc_C7D6 берёт указатель объекта из DE, а не из HL.
# ---------------------------------------------------------------------------
N_UPD_3_5 = """upd_3_5:		; дальняя сторона арки: hflip?
				ld		hl, (z80_ix)
				ld		a, l
				add		a, 7
				ld		l, a
				ld		a, (hl)
				and		0x40								; hflip?
				jp		nz, adj_3_5_hflip
				ld		hl, 0xFDF7							; -3, -9
"""

N_UPD_2_4 = """upd_2_4:		; ближняя сторона арки: hflip?
				ld		hl, (z80_ix)
				ld		a, l
				add		a, 7
				ld		l, a
				ld		a, (hl)
				and		0x40								; hflip?
				jp		nz, adj_2_4_hflip
				ld		hl, (z80_ix)
				ld		a, (hl)								; graphic no
				cp		4									; tree arch?
				jp		z, adj_m3_p1
				ld		hl, 0xFDF9							; -3, -7

loc_C74C:		call	set_pixel_adj
				ld		hl, (z80_ix)
				inc		hl
				inc		hl									; ix+2 (Y)
				ld		a, (hl)
				add		a, 13
				ld		c, a
				ld		a, l
				add		a, 8
				ld		l, a								; ix+10
				ld		(hl), c								; dY = Y+13
				ld		a, l
				sub		9
				ld		l, a								; ix+1 (X)
				ld		a, (hl)
				ld		c, a
				ld		a, l
				add		a, 8
				ld		l, a								; ix+9
				ld		(hl), c								; dX = X
				ld		hl, 0x60F							; +6, +15

loc_C760:		ex		de, hl								; DE = пороги is_near_to
				ld		hl, (z80_ix)
				inc		hl
				inc		hl
				inc		hl									; ix+3 (Z)
				ld		a, (hl)
				ld		c, a
				ld		a, l
				add		a, 8
				ld		l, a								; ix+11
				ld		(hl), c								; dZ = Z
				ex		de, hl								; HL = пороги
				call	chk_plyr_spec_near_arch
				jp		loc_C785

adj_2_4_hflip:	; -2, -17
				ld		hl, 0xFEEF
				call	set_pixel_adj
				ld		hl, (z80_ix)
				inc		hl									; ix+1 (X)
				ld		a, (hl)
				sub		13
				ld		c, a
				ld		a, l
				add		a, 8
				ld		l, a								; ix+9
				ld		(hl), c								; dX = X-13
				ld		a, l
				sub		7
				ld		l, a								; ix+2 (Y)
				ld		a, (hl)
				ld		c, a
				ld		a, l
				add		a, 8
				ld		l, a								; ix+10
				ld		(hl), c								; dY = Y
				ld		hl, 0xF06							; +15, +6
				jp		loc_C760

loc_C785:		; +15, +15
				ld		hl, 0xF0F
				ld		de, graphic_objs_tbl
				ld		b, 4								; игрок и спецобъекты

loc_C791:		ld		a, (de)								; graphic no
				or		a									; null?
				jp		z, loc_C7D6
				push	hl									; пороги
				ld		h, d
				ld		l, e
				ld		(z80_iy), hl						; объект для is_near_to и adj_ew/adj_ns
				ld		a, l
				add		a, 7
				ld		l, a
				ld		a, (hl)
				and		8									; auto-adjust?
				pop		hl
				jp		z, loc_C7D6
				call	is_near_to
				jp		nc, loc_C7D6
				push	bc
				ld		bc, adj_arch_tbl
				jp		lookup_plyr_dXY						; переход по направлению спрайта
; ---------------------------------------------------------------------------
adj_arch_tbl:   DW adj_ew
				DW adj_ew
				DW adj_ns
				DW adj_ns
; ---------------------------------------------------------------------------

adj_ew:                                                         ; dY
				ld      a, (ix+10)
				cp      (iy+2)                                   ; Y
				jr      Z, loc_C7D5
				ld      a, 1
				jr      NC, loc_C7BF
				neg

loc_C7BF:
				ld      (iy+15), a
				jr      loc_C7D5
; ---------------------------------------------------------------------------

adj_ns:                                                         ; dX
				ld      a, (ix+9)
				cp      (iy+1)                                   ; X
				jr      Z, loc_C7D5
				ld      a, 1
				jr      NC, loc_C7D2
				neg

loc_C7D2:
				ld      (iy+14), a

loc_C7D5:
				pop     bc

loc_C7D6:		ld		a, e								; следующий объект (указатель в DE)
				add		a, 32
				ld		e, a
				ld		a, d
				adc		a, 0
				ld		d, a
				dec		b
				jp		nz, loc_C791
				ld		hl, graphic_objs_tbl+128			; IY после цикла — как у оригинала
				ld		(z80_iy), hl
				ret

; =============== S U B R O U T I N E =======================================


"""


def _sub(src, pattern, repl, name, flags=re.S):
    src, n = re.subn(pattern, lambda m: repl, src, count=1, flags=flags)
    assert n == 1, "kl_native_logic: не найдено: " + name
    return src


def apply(src):
    src = _sub(src, r'(?m)^is_near_to:\n.*?; End of function is_near_to\r?\n', N_IS_NEAR_TO, 'is_near_to')
    src = _sub(src, r'(?m)^chk_plyr_spec_near_arch:\n.*?; End of function chk_plyr_spec_near_arch\r?\n',
               N_CHK_NEAR_ARCH, 'chk_plyr_spec_near_arch')
    src = _sub(src, r'(?m)^set_draw_objs_overlapped:.*?; End of function set_draw_objs_overlapped\r?\n',
               N_SET_DRAW, 'set_draw_objs_overlapped')
    src = _sub(src, r'(?m)^upd_3_5:[^\n]*\n.*?(?=^; =============== S U B R O U T I N E)', N_UPD_3_5, 'upd_3_5')
    src = _sub(src, r'(?m)^upd_2_4:[^\n]*\n.*?(?=^chk_plyr_spec_near_arch:)', N_UPD_2_4, 'upd_2_4 + цикл арки')
    # ret_from_tbl_jp: байтовый инкремент z80_ix вместо 16-битного, сравнение по младшему байту
    src = _sub(src, r'(?m)^ret_from_tbl_jp:.*?(?=^loc_B000:)', N_RET_FROM_TBL, 'ret_from_tbl_jp')
    return src
