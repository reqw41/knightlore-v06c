# -*- coding: utf-8 -*-
"""Нативные i8080-процедуры Knight Lore (logic): применяются к транслируемому тексту после kl_native.apply.
Метод: спецификация на Python (spec/kl_spec_logic.py) -> дамп вызовов (tools/calldump_vec.js) -> asm; см. docs/PORT_RECIPE.md."""
import re

SCRATCH = """
kl_wl:			DS		1			; wipe_next_object: ширина стирания (уходит в L)
kl_wh:			DS		1			; wipe_next_object: высота стирания (уходит в H) — обязана идти сразу за kl_wl
kl_scan:		DS		2			; calc_display_order_and_render: первая ещё не отрисованная запись objects_to_draw
kl_cpcnt:		DS		1			; loc_D653/.copy_loop: счётчик копий (стек занят параметрами blit_to_screen)
kl_seed0:		DS		1			; START: байт-затравка seed_1, переживающий clr_mem
kl_nobj:		DB		40			; сколько записей graphic_objs_tbl реально занято (см. zero_end_of_graphic_objs_tbl)
"""

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
sd_nb:			ld		b, 40								; иммедиат: занятые записи таблицы (правит zeg_got)
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
				jp		nc, sd_p26
				inc		h
sd_p26:			ld		a, (hl)								; +26 pixel X
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


# ---------------------------------------------------------------------------
# КЛАСС ПЕРЕСЕЧЕНИЙ (do_objs_intersect_on_x/y/z + их четыре цикла).
#
# Оригинал считает |коорд1 + d - коорд2| - (размер1 + размер2), где «1» — движущийся
# объект (IX), «2» — очередной объект таблицы (IY), d — правимое смещение (C/L/H).
# Шесть полей объекта 1 (X1,Y1,Z1,W1,D1,H1) и три смещения внутри одного прохода цикла
# НЕ МЕНЯЮТСЯ: ветка попадания правит только (ix+12), (ix+13), (iy+9/10/13) и, редко,
# одно из d. Поэтому объект 1 целиком уходит в иммедиаты (как прямоугольник в ov_prep):
#
#   КОНТРАКТ (5 сайтов вызова: do_any_objs_intersect, is_on_or_near_obj,
#             adj_d{X,Y,Z}_for_obj_intersect):
#     * перед серией вызовов — call doi_prep (C = dX, L = dY, H = dZ, объект 1 в z80_ix);
#       он кладёт в иммедиаты doix_s+1 = X1+dX, doiy_s+1 = Y1+dY, doiz_s+1 = Z1+dZ,
#       doix_w+1 = W1, doiy_d+1 = D1, doiz_h+1 = H1. HL и BC целы, DE портит;
#     * do_objs_intersect_on_{x,y,z} принимают БАЗУ ОБЪЕКТА 2 В HL (а не в z80_iy) и
#       возвращают HL целым; портят только A и флаги (CY = пересечение). BC, DE целы —
#       поэтому в циклах DE держит шаг 32 (add hl,de), а D больше не нужен под сумму
#       размеров: она уходит в иммедиат doi{x,y}_e+1;
#     * если цикл поменял своё d (ветка попадания, ~раз на итерацию), он правит
#       соответствующий иммедиат НА ДЕЛЬТУ (adj_d_for_out_of_bounds меняет d на +-1),
#       а не пересчитывает prep;
#     * z80_iy больше не ведётся по объектам: его значение восстанавливается на выходе
#       из цикла (конец таблицы) и в ветке попадания (для хелперов il_iy_*) — ровно
#       те два значения, которые видел бы вызыватель у оригинала.
#
# Цена: 196 -> 105/114 (x), 110/119 (y), 86/92 (z) тактов на вызов; сам проход цикла
# 149 -> 46 (пустой объект) / 79 (проверяемый). doi_prep — 196 тактов, ~8 раз за итерацию.
# ---------------------------------------------------------------------------
N_DOI_X = """doi_prep:		; иммедиаты класса пересечений по (z80_ix) и C/L/H (dX/dY/dZ). HL, BC целы; DE портит.
				ex		de, hl								; DE = dY(E)/dZ(D)
				ld		hl, (z80_ix)
				inc		hl									; ix+1
				ld		a, (hl)
				add		a, c
				ld		(doix_s+1), a						; X1 + dX
				inc		hl									; ix+2
				ld		a, (hl)
				add		a, e
				ld		(doiy_s+1), a						; Y1 + dY
				inc		hl									; ix+3
				ld		a, (hl)
				add		a, d
				ld		(doiz_s+1), a						; Z1 + dZ
				inc		hl									; ix+4
				ld		a, (hl)
				ld		(doix_w+1), a						; W1
				inc		hl									; ix+5
				ld		a, (hl)
				ld		(doiy_d+1), a						; D1
				inc		hl									; ix+6
				ld		a, (hl)
				ld		(doiz_h+1), a						; H1
				ex		de, hl								; HL = dY/dZ обратно
				ret
; End of function doi_prep

do_objs_intersect_on_x:	; HL = база объекта 2 (цела); CY = пересечение по X. Портит A.
				ld		a, l
				add		a, 4
				ld		l, a								; obj2+4 (thisW), переноса нет — ASSERT в set_draw_objs_overlapped
doix_w:			ld		a, 0								; W1
				add		a, (hl)								; W1 + thisW
				ld		(doix_e+1), a
				dec		hl
				dec		hl
				dec		hl									; obj2+1 (thisX)
doix_s:			ld		a, 0								; X1 + dX
				sub		(hl)
				jp		p, doix_1
				cpl
				inc		a									; abs()
doix_1:			dec		hl									; -> база (dec hl флаги не портит)
doix_e:			sub		0									; - (W1 + thisW)
				ret
; End of function do_objs_intersect_on_x
"""

N_DOI_Y = """do_objs_intersect_on_y:	; HL = база объекта 2 (цела); CY = пересечение по Y. Портит A.
				ld		a, l
				add		a, 5
				ld		l, a								; obj2+5 (thisD)
doiy_d:			ld		a, 0								; D1
				add		a, (hl)								; D1 + thisD
				ld		(doiy_e+1), a
				dec		hl
				dec		hl
				dec		hl									; obj2+2 (thisY)
doiy_s:			ld		a, 0								; Y1 + dY
				sub		(hl)
				jp		p, doiy_1
				cpl
				inc		a
doiy_1:			dec		hl
				dec		hl									; -> база
doiy_e:			sub		0
				ret
; End of function do_objs_intersect_on_y
"""

N_DOI_Z = """do_objs_intersect_on_z:	; HL = база объекта 2 (цела); CY = пересечение по Z. Портит A.
				push	hl
				inc		hl
				inc		hl
				inc		hl									; obj2+3 (thisZ)
doiz_s:			ld		a, 0								; Z1 + dZ
				sub		(hl)
				jp		p, doiz_pos
				cpl
				inc		a									; abs()
doiz_h:			sub		0									; - H1 (высота объекта 1)
				pop		hl
				ret
doiz_pos:		inc		hl
				inc		hl
				inc		hl									; obj2+6 (thisH)
				sub		(hl)								; - thisH
				pop		hl
				ret
; End of function do_objs_intersect_on_z
"""

# ---------------------------------------------------------------------------
# Циклы класса пересечений. Общая голова прохода (граф. номер != 0 и не «ignore»)
# развёрнута по месту: указатель живёт в HL, шаг 32 — в DE, счётчик — в B,
# а dY/dZ (у adj_dX) или пара невыделенных d — на стеке (ex (sp),hl в ветке попадания).
# Ветка попадания оставлена транслированной (хелперы il_ix_*/il_iy_* сами берегут HL/BC/DE),
# ей достаточно один раз положить указатель в z80_iy.
# ---------------------------------------------------------------------------
def _loop_head(pre, nx, nx7):
    return ("%s_lp:\t\t\tld\t\ta, (hl)\t\t\t\t\t\t\t\t; graphic no\n"
            "\t\t\t\tand\t\ta\n"
            "\t\t\t\tjp\t\tz, %s\n"
            "\t\t\t\tld\t\ta, l\n"
            "\t\t\t\tadd\t\ta, 7\n"
            "\t\t\t\tld\t\tl, a\t\t\t\t\t\t\t\t; obj2+7 (флаги)\n"
            "\t\t\t\tld\t\ta, (hl)\n"
            "\t\t\t\tand\t\t2\t\t\t\t\t\t\t\t\t; ignore?\n"
            "\t\t\t\tjp\t\tnz, %s\n"
            "\t\t\t\tld\t\ta, l\n"
            "\t\t\t\tsub\t\t7\n"
            "\t\t\t\tld\t\tl, a\t\t\t\t\t\t\t\t; -> база объекта 2\n") % (pre, nx, nx7)


def _loop_tail(pre, nx, nx7, done):
    return ("%s:\t\t\tld\t\ta, l\n"
            "\t\t\t\tsub\t\t7\n"
            "\t\t\t\tld\t\tl, a\n"
            "%s:\t\t\tadd\t\thl, de\t\t\t\t\t\t\t\t; следующий объект\n"
            "\t\t\t\tdec\t\tb\n"
            "\t\t\t\tjp\t\tnz, %s_lp\n"
            "\t\t\t\tld\t\thl, graphic_objs_tbl+1280\n"
            "\t\t\t\tld\t\t(z80_iy), hl\t\t\t\t\t\t; IY после цикла — как у оригинала\n"
            "%s:\t\t\tpop\t\thl\t\t\t\t\t\t\t\t\t; dY/dZ\n"
            "\t\t\t\tret\n") % (nx7, nx, pre, done)


# ---------------------------------------------------------------------------
# ПЕРВАЯ ОСЬ ВСТРОЕНА В ПРОХОД, а проверка «не игнорировать» СДВИНУТА ЗА НЕЁ.
#
# Замер комнаты 39: из 40 объектов 25 живых, из них первую ось проходят единицы
# (doi_x зовётся 95 раз за итерацию — ровно столько, сколько живых проходов adj_dY,
# то есть ВТОРАЯ ось почти никогда не достигается). Значит цена прохода = цена
# первой оси, и её надо резать:
#   * call/ret (27) и ld a,l/sub 7/ld l,a возврата к базе (15) убраны — тест
#     вкомпилирован в цикл;
#   * указатель идёт +0 -> +S (размер) -> +K (координата) -> +0 монотонно
#     (dec l по 5 тактов вместо ld a,l/add/ld l,a по 15);
#   * бит «ignore» читается ТОЛЬКО у тех, кто прошёл первую ось. Порядок проверок
#     менять можно: is_object_not_ignored не имеет побочных эффектов, а объект,
#     отсеянный любой из двух проверок, одинаково уходит на следующий.
# Проход «живой, отсеян по первой оси»: 235 -> 154 (adj_dX) / 149 (adj_dY, adj_dZ).
# Пустой объект — 46 тактов, как было.
#
# Иммедиаты первой оси — СВОИ КОПИИ (adx_s/adx_d, ady_s/ady_w, adz_s/adz_w): их
# заполняет пролог цикла из иммедиатов doi_prep (26 тактов на копию, 2 копии на вызов).
# Копии, а не общие ячейки: ветка попадания цикла правит СВОЙ doi{x,y,z}_s на дельту,
# а первая ось у каждого цикла — чужая ось, которая внутри цикла не меняется.
# ---------------------------------------------------------------------------
def _axis_inline(pre, S, K, sz, co):
    """Первая ось прохода: |co1 - (obj2+K)| - (sz1 + (obj2+S)), S = K+3."""
    assert S == K + 3
    return (
        "%s_lp:\t\t\tld\t\ta, (hl)\t\t\t\t\t\t\t\t; graphic no\n"
        "\t\t\t\tand\t\ta\n"
        "\t\t\t\tjp\t\tz, %s_nx\n"
        "\t\t\t\tld\t\ta, l\n"
        "\t\t\t\tadd\t\ta, %d\n"
        "\t\t\t\tld\t\tl, a\t\t\t\t\t\t\t\t; obj2+%d (%s2)\n"
        "%s:\t\t\tld\t\ta, 0\t\t\t\t\t\t\t\t; %s1\n"
        "\t\t\t\tadd\t\ta, (hl)\n"
        "\t\t\t\tld\t\t(%s_e+1), a\t\t\t\t\t\t; %s1+%s2\n"
        "\t\t\t\tdec\t\tl\n"
        "\t\t\t\tdec\t\tl\n"
        "\t\t\t\tdec\t\tl\t\t\t\t\t\t\t\t; obj2+%d (%s2)\n"
        "%s_s:\t\t\tld\t\ta, 0\t\t\t\t\t\t\t\t; %s1 + d\n"
        "\t\t\t\tsub\t\t(hl)\n"
        "\t\t\t\tjp\t\tp, %s_a\n"
        "\t\t\t\tcpl\n"
        "\t\t\t\tinc\t\ta\t\t\t\t\t\t\t\t\t; abs()\n"
        "%s_a:\n"
        "%s_e:\t\t\tsub\t\t0\n"
        "\t\t\t\tjp\t\tnc, %s_nk\t\t\t\t\t\t\t; нет пересечения по первой оси\n"
        "\t\t\t\tld\t\ta, l\n"
        "\t\t\t\tadd\t\ta, %d\n"
        "\t\t\t\tld\t\tl, a\t\t\t\t\t\t\t\t; obj2+7 (флаги)\n"
        "\t\t\t\tld\t\ta, (hl)\n"
        "\t\t\t\tand\t\t2\t\t\t\t\t\t\t\t\t; ignore?\n"
        "\t\t\t\tjp\t\tnz, %s_nx7\n"
        "\t\t\t\tld\t\ta, l\n"
        "\t\t\t\tsub\t\t7\n"
        "\t\t\t\tld\t\tl, a\t\t\t\t\t\t\t\t; -> база объекта 2\n"
    ) % (pre, pre, S, S, sz, pre + '_z', sz, pre, sz, sz, K, co,
         pre, co, pre, pre, pre, pre, 7 - K, pre)


def _axis_tail(pre, K, nx, nx7, done):
    # nx7 (объект «ignore», HL на +7) снимает 7-K и ПРОВАЛИВАЕТСЯ в nk, который снимает
    # оставшиеся K: общий хвост, лишнего jp нет.
    return ("%s:\t\t\tld\t\ta, l\n"
            "\t\t\t\tsub\t\t%d\n"
            "\t\t\t\tld\t\tl, a\t\t\t\t\t\t\t\t; +7 -> +%d\n"
            "%s_nk:\t\t\t" + "dec\t\tl\n\t\t\t\t" * K + "; -> база объекта 2\n"
            "%s:\t\t\tadd\t\thl, de\t\t\t\t\t\t\t\t; следующий объект\n"
            "\t\t\t\tdec\t\tb\n"
            "\t\t\t\tjp\t\tnz, %s_lp\n"
            "\t\t\t\tld\t\thl, graphic_objs_tbl+1280\n"
            "\t\t\t\tld\t\t(z80_iy), hl\t\t\t\t\t\t; IY после цикла — как у оригинала\n"
            "%s:\t\t\tpop\t\thl\t\t\t\t\t\t\t\t\t; dY/dZ\n"
            "\t\t\t\tret\n") % (nx7, 7 - K, K, pre, nx, pre, done)


def _axis_prep(pre, src_s, src_sz):
    return ("\t\t\t\tld\t\ta, (%s+1)\n"
            "\t\t\t\tld\t\t(%s_s+1), a\t\t\t\t\t\t; координата+d первой оси — в свой иммедиат\n"
            "\t\t\t\tld\t\ta, (%s+1)\n"
            "\t\t\t\tld\t\t(%s_z+1), a\t\t\t\t\t\t; размер объекта 1 по первой оси\n"
            ) % (src_s, pre, src_sz, pre)



# ---------------------------------------------------------------------------
# «Редкая» ветка осей (объекты пересеклись) НАТИВНО (§31.2). Она не такая уж редкая:
# tools/ilhot_vec.js в комнате 163 даёт 59 из 66 исполнений хелперов (ix+d)/(iy+d) на
# итерацию именно здесь (adz_rt 5.5 прохода на итерацию по 8..14 обращений). Через
# хелперы одно обращение стоит ~300..480 тактов, здесь всё делается двумя указателями:
# HL и DE ходят по полям объектов, а BC занят (B — счётчик объектов, C — dX) и стек
# держит dY/dZ, поэтому obj2+13 на время расчёта obj1 уходит в стек (push/pop).
# Порядок чтений и записей сохранён ДОСЛОВНО (объекты 1 и 2 могут совпасть только
# в теории, но переставлять обращения всё равно не нужно).
# Цена: +7 Б (adz) и +12 Б (adx, ady) кода против -5.5 % тактов в комнате 163.
# ---------------------------------------------------------------------------


def _axis_hit(pre, oobbit, copyoff, exitlbl, zflag=False, dycopy=False):
    """Нативная ветка «объекты пересеклись» для оси pre (adx/ady/adz).
       oobbit — бит OOB в поле 12 объекта 1; copyoff — поле, которое копируется;
       zflag — ось Z: ещё и `set 3,(iy+13)`, подвижность смотрится у объекта 1,
       копия идёт ИЗ объекта 2 В объект 1 и только если поле уже нулевое."""
    T = "\t\t\t\t"
    o = []
    o.append(T + "ld\t\t(z80_iy), hl\t\t\t\t\t\t; IY = объект 2 (как в оригинале)")
    o.append(T + "ld\t\tde, 13")
    o.append(T + "add\t\thl, de\t\t\t\t\t\t\t\t; obj2+13")
    o.append(T + "push\thl\t\t\t\t\t\t\t\t\t; BC занят (счётчик и dX) — obj2+13 на стек")
    o.append(T + "ld\t\thl, (z80_ix)")
    o.append(T + "add\t\thl, de\t\t\t\t\t\t\t\t; obj1+13")
    o.append(T + "dec\t\thl\t\t\t\t\t\t\t\t\t; obj1+12")
    o.append(T + "ld\t\ta, (hl)")
    o.append(T + "or\t\t%d\t\t\t\t\t\t\t\t\t; set %d,(ix+12): ось вышла за границу" % (1 << oobbit, oobbit))
    o.append(T + "ld\t\t(hl), a")
    o.append(T + "inc\t\thl\t\t\t\t\t\t\t\t\t; obj1+13")
    o.append(T + "ld\t\ta, (hl)")
    o.append(T + "rrca")
    o.append(T + "and\t\t0x40\t\t\t\t\t\t\t\t; бит 7 -> 6")
    o.append(T + "ex\t\tde, hl\t\t\t\t\t\t\t\t; DE = obj1+13")
    o.append(T + "pop\t\thl\t\t\t\t\t\t\t\t\t; HL = obj2+13")
    o.append(T + "or\t\t(hl)\t\t\t\t\t\t\t\t; объект 2 разрушен?")
    o.append(T + "ld\t\t(hl), a")
    o.append(T + "rlca")
    o.append(T + "and\t\t0x40\t\t\t\t\t\t\t\t; бит 5 -> 6")
    o.append(T + "ex\t\tde, hl\t\t\t\t\t\t\t\t; HL = obj1+13, DE = obj2+13")
    o.append(T + "or\t\t(hl)\t\t\t\t\t\t\t\t; объект 1 разрушен?")
    o.append(T + "ld\t\t(hl), a")
    if zflag:
        o.append(T + "ld\t\ta, (de)")
        o.append(T + "or\t\t8\t\t\t\t\t\t\t\t\t; set 3,(iy+13): сработало (падение, рассыпание)")
        o.append(T + "ld\t\t(de), a")
        o.append("\n".join([T + "dec\t\tde"] * 4) + "\t\t\t\t\t\t\t; obj2+9 (нужен обоим путям)")
        # подвижность смотрится у объекта 1 (ix+7): HL = obj1+13 -> -6
        o.append("\n".join([T + "dec\t\thl"] * 6) + "\t\t\t\t\t\t\t; obj1+7")
        o.append(T + "ld\t\ta, (hl)")
        o.append(T + "and\t\t4\t\t\t\t\t\t\t\t\t; bit 2,(ix+7): подвижен?")
        o.append(T + "jp\t\tz, %s_hlx" % pre)
        o.append("\n".join([T + "inc\t\thl"] * 2) + "\t\t\t\t\t\t\t; obj1+9")
        o.append(T + "ld\t\ta, (hl)")
        o.append(T + "and\t\ta\t\t\t\t\t\t\t\t\t; dX ненулевой?")
        o.append(T + "jp\t\tnz, %s_c1" % pre)
        o.append(T + "ld\t\ta, (de)")
        o.append(T + "ld\t\t(hl), a\t\t\t\t\t\t\t\t; копия dX от объекта 2")
        o.append("%s_c1:\t\tinc\t\tde\t\t\t\t\t\t\t\t; obj2+10" % pre)
        o.append(T + "inc\t\thl\t\t\t\t\t\t\t\t\t; obj1+10")
        o.append(T + "ld\t\ta, (hl)")
        o.append(T + "and\t\ta\t\t\t\t\t\t\t\t\t; движется по Y?")
        o.append(T + "jp\t\tnz, %s_hlx" % pre)
        o.append(T + "ld\t\ta, (de)")
        o.append(T + "ld\t\t(hl), a\t\t\t\t\t\t\t\t; копия dY от объекта 2")
    else:
        # подвижность смотрится у объекта 2 (iy+7): DE = obj2+13 -> -6
        o.append("\n".join([T + "dec\t\tde"] * 6) + "\t\t\t\t\t\t\t; obj2+7")
        o.append(T + "ld\t\ta, (de)")
        o.append(T + "and\t\t4\t\t\t\t\t\t\t\t\t; bit 2,(iy+7): подвижен?")
        o.append(T + "jp\t\tz, %s_hlx" % pre)
        o.append("\n".join([T + "dec\t\thl"] * (13 - copyoff)) + "\t\t\t\t\t\t\t; obj1+%d" % copyoff)
        o.append(T + "ld\t\ta, (hl)")
        o.append("\n".join([T + "inc\t\tde"] * (copyoff - 7)) + "\t\t\t\t\t\t\t; obj2+%d" % copyoff)
        o.append(T + "ld\t\t(de), a\t\t\t\t\t\t\t\t; копия d%s объекту 2" % ('X' if copyoff == 9 else 'Y'))
    # ХВОСТ. Оба выхода ветки обязаны вернуть HL = УКАЗАТЕЛЬ ОБЪЕКТА 2: loc_CC28/loc_CC8D
    # меняют его со стеком (`ex (sp),hl`), а adx_rt идёт с ним на следующий do_objs_intersect.
    # Оригинал держал указатель в HL всю ветку (обращения шли через хелперы (ix+d)/(iy+d),
    # а те HL восстанавливают); нативная ветка HL расходует, поэтому база берётся из z80_iy.
    # Цена ошибки: указатель цикла уезжал в поле объекта — локстеп разошёлся на 78-й итерации.
    o.append("%s_hlx:\t\tld\t\thl, (z80_iy)\t\t\t\t\t; вернуть указатель объекта 2" % pre)
    return "\n".join(o) + "\n"

N_ADJ_DX_LOOP = """adj_dX_for_obj_intersect:	; C = dX (правится), HL = dY/dZ; прижать dX к другим объектам
				call	doi_prep
""" + _axis_prep('adx', 'doiy_s', 'doiy_d') + """				push	hl									; dY/dZ — на стек, HL под указатель
				ld		hl, graphic_objs_tbl
				ld		de, 32
adx_nb:			ld		b, 40								; иммедиат: занятые записи таблицы (правит zeg_got)
""" + _axis_inline('adx', 5, 2, 'D', 'Y') + """				call    do_objs_intersect_on_z
				jp		nc, adx_nx

adx_rt:			call    do_objs_intersect_on_x
				jp		nc, adx_nx
""" + _axis_hit('adx', 0, 9, 'loc_CBD9') + """
loc_CBD9:		ld		a, c
				call    adj_d_for_out_of_bounds
				ld		d, a								; новый dX
				sub		c									; дельта (+-1)
				ld		c, d
				ld		e, a
				ld		a, (doix_s+1)
				add		a, e
				ld		(doix_s+1), a						; X1+dX — на дельту
				ld		a, d
				and		a									; dX стал нулём?
				ld		de, 32								; вернуть шаг (флаги целы)
				jp		z, adx_done
				jp		adx_rt
""" + _axis_tail('adx', 2, 'adx_nx', 'adx_nx7', 'adx_done') + """; End of function adj_dX_for_obj_intersect
"""

N_ADJ_DY_LOOP = """adj_dY_for_obj_intersect:	; L = dY (правится), C = dX, H = dZ; прижать dY к другим объектам
				call	doi_prep
""" + _axis_prep('ady', 'doix_s', 'doix_w') + """				push	hl									; dY/dZ — на стек, HL под указатель
				ld		hl, graphic_objs_tbl
				ld		de, 32
ady_nb:			ld		b, 40								; иммедиат: занятые записи таблицы (правит zeg_got)
""" + _axis_inline('ady', 4, 1, 'W', 'X') + """				call    do_objs_intersect_on_z
				jp		nc, ady_nx

ady_rt:			call    do_objs_intersect_on_y
				jp		nc, ady_nx
""" + _axis_hit('ady', 1, 10, 'loc_CC28') + """
loc_CC28:		ex		(sp), hl							; HL = dY/dZ, указатель — на стек
				ld		a, l
				call    adj_d_for_out_of_bounds
				ld		d, a								; новый dY
				sub		l									; дельта (+-1)
				ld		l, d
				ld		e, a
				ld		a, (doiy_s+1)
				add		a, e
				ld		(doiy_s+1), a						; Y1+dY — на дельту
				ld		a, d
				and		a									; dY стал нулём?
				ex		(sp), hl							; HL = указатель обратно (флаги целы)
				ld		de, 32
				jp		z, ady_done
				jp		ady_rt
""" + _axis_tail('ady', 1, 'ady_nx', 'ady_nx7', 'ady_done') + """; End of function adj_dY_for_obj_intersect
"""

N_ADJ_DZ_LOOP = """adj_dZ_for_obj_intersect:	; H = dZ (правится), C = dX, L = dY; прижать dZ к другим объектам
				call	doi_prep
""" + _axis_prep('adz', 'doix_s', 'doix_w') + """				push	hl									; dY/dZ — на стек, HL под указатель
				ld		hl, graphic_objs_tbl
				ld		de, 32
adz_nb:			ld		b, 40								; иммедиат: занятые записи таблицы (правит zeg_got)
""" + _axis_inline('adz', 4, 1, 'W', 'X') + """				call    do_objs_intersect_on_y
				jp		nc, adz_nx

adz_rt:			call    do_objs_intersect_on_z
				jp		nc, adz_nx
""" + _axis_hit('adz', 2, 9, 'loc_CC8D', zflag=True) + """
loc_CC8D:		ex		(sp), hl							; HL = dY/dZ, указатель — на стек
				ld		a, h
				call    adj_d_for_out_of_bounds
				ld		d, a								; новый dZ
				sub		h									; дельта (+-1)
				ld		h, d
				ld		e, a
				ld		a, (doiz_s+1)
				add		a, e
				ld		(doiz_s+1), a						; Z1+dZ — на дельту
				ld		a, d
				and		a									; dZ стал нулём?
				ex		(sp), hl							; HL = указатель обратно (флаги целы)
				ld		de, 32
				jp		z, adz_done
				jp		adz_rt
""" + _axis_tail('adz', 1, 'adz_nx', 'adz_nx7', 'adz_done') + """; End of function adj_dZ_for_obj_intersect
"""

# do_any_objs_intersect: тот же цикл, но HL уже сохранён прологом (push hl), а IY —
# push/pop iy, поэтому z80_iy на выходе восстанавливать не нужно.
N_DO_ANY = """do_any_objs_intersect:
				push    bc
				push    de
				push    hl
				push    iy
				set     1, (ix+7)                                ; ignore this object
				ld      c, 0                                   ; dX=0
				ld      l, c                                    ; dY=0
				ld      h, c                                    ; dZ=0
				call	doi_prep
				ld		hl, graphic_objs_tbl
				ld		de, 32
dai_nb:			ld		b, 40								; иммедиат: занятые записи таблицы (правит zeg_got)
""" + _loop_head('dai', 'dai_nx', 'dai_nx7') + """				call    do_objs_intersect_on_x
				jp		nc, dai_nx
				call    do_objs_intersect_on_y
				jp		nc, dai_nx
				call    do_objs_intersect_on_z
				jp		nc, dai_nx

loc_B524:
				pop     iy
				pop     hl
				pop     de
				pop     bc
				res     1, (ix+7)                                ; reset ignore flag
				ret                                             ; C flag set indicates intersection
; ---------------------------------------------------------------------------

dai_nx7:		ld		a, l
				sub		7
				ld		l, a
dai_nx:			add		hl, de
				dec		b
				jp		nz, dai_lp
				and		a
				jp		loc_B524
; End of function do_any_objs_intersect
"""

# is_on_or_near_obj: dX=dY=dZ=0, объект 2 — в z80_iy (его задаёт вызыватель).
# Оригинал ради проверки по Z временно занижал (ix+3) на 4 — теперь достаточно
# занизить иммедиат doiz_s+1 (при dZ=0 он и есть Z1), память объекта не трогается.
# HL у оригинала на выходе = 0 — восстанавливаем (lxi флаги не портит).
N_ON_OR_NEAR = """is_on_or_near_obj:
				push    bc
				ld      bc, 0
				ld      l, c
				ld      h, c
				call	doi_prep
				ld		hl, (z80_iy)						; база объекта 2
				call    do_objs_intersect_on_x
				jp		nc, loc_C19F
				call    do_objs_intersect_on_y
				jp		nc, loc_C19F
				ld		a, (doiz_s+1)
				sub		4									; Z-4
				ld		(doiz_s+1), a
				call    do_objs_intersect_on_z

loc_C19F:		ld		hl, 0								; HL после оригинала = 0
				pop     bc
				ret
; End of function can_pickup_spec_obj
"""

# ---------------------------------------------------------------------------
# chk_plyr_OOB: CY = игрок внутри комнаты. Оригинал портит A, HL (оставляет в HL
# room_size-минус-габарит по X и Y) и флаги; BC и DE целы — значит габариты можно
# посчитать в BC, сохранив BC на стеке, и вернуть их в HL перед ret (MOV флагов не трогает).
# ---------------------------------------------------------------------------
N_CHK_OOB = """chk_plyr_OOB:	; CY = внутри комнаты; A = |Y-128|; HL = (sizeX-W, sizeY-D). BC/DE целы.
				push	bc
				ld		hl, (z80_ix)
				ld		a, l
				add		a, 4
				ld		l, a								; ix+4
				ld		b, (hl)								; width
				inc		hl
				ld		c, (hl)								; depth
				ld		hl, (room_size_X)					; L = room_size_X, H = room_size_Y
				ld		a, l
				sub		b
				ld		b, a								; B = size X - width
				ld		a, h
				sub		c
				ld		c, a								; C = size Y - depth
				ld		hl, (z80_ix)
				inc		hl									; ix+1
				ld		a, (hl)								; X
				sub		128
				jp		p, cpo_1
				cpl
				inc		a
cpo_1:			cp		b
				jp		nc, cpo_out							; OOB
				inc		hl									; ix+2
				ld		a, (hl)								; Y
				sub		128
				jp		p, cpo_2
				cpl
				inc		a
cpo_2:			cp		c
cpo_out:		ld		l, b
				ld		h, c
				pop		bc
				ret
; End of function chk_plyr_OOB
"""

# ---------------------------------------------------------------------------
# clear_dX_dY (хвост move_player): dX = dY = 0. Оригинал портит только A (=0) и флаги.
# ---------------------------------------------------------------------------
N_CLEAR_DXDY = """clear_dX_dY:
				push	hl
				ld		hl, (z80_ix)
				ld		a, l
				add		a, 9
				ld		l, a								; ix+9
				xor		a
				ld		(hl), a								; dX = 0
				inc		hl
				ld		(hl), a								; dY = 0
				pop		hl
				ret
; End of function move_player
"""

# ---------------------------------------------------------------------------
# adj_dZ_for_out_of_bounds и adj_for_out_of_bounds. Здесь HL занят рабочими значениями
# (L = dY, H = dZ), поэтому указатель объекта берётся во временное владение HL:
# в прологе adj_for_out_of_bounds HL ещё свободен, дальше — через ex de,hl (DE в этих
# процедурах мёртв: adj_dZ_for_out_of_bounds сама кладёт в D room_size_Z) или push/pop hl.
# ---------------------------------------------------------------------------
N_ADJ_DZ_OOB = """adj_dZ_for_out_of_bounds:	; H = dZ; прижать dZ так, чтобы Z+dZ не вышел за room_size_Z
				ld		a, (room_size_Z)
				ld		d, a

loc_CA5E:		push	hl
				ld		hl, (z80_ix)
				inc		hl
				inc		hl
				inc		hl									; ix+3
				ld		a, (hl)								; Z
				pop		hl
				add		a, h								; Z += dZ
				cp		d									; >= высоты комнаты?
				ret		nc									; да, выходим
				push	hl
				ld		hl, (z80_ix)
				ld		a, l
				add		a, 12
				ld		l, a								; ix+12
				ld		a, (hl)
				or		4									; set 2 — вышли по Z
				ld		(hl), a
				pop		hl
				ld		a, h								; Z+dZ
				call	adj_d_for_out_of_bounds
				ld		h, a								; новый dZ
				jp		nz, loc_CA5E						; проверить ещё раз
				ret
; End of function adj_dZ_for_out_of_bounds
"""

# ---------------------------------------------------------------------------
# adj_d{X,Y}_for_out_of_bounds: прижать d так, чтобы |коорд+d-128| + габарит не вышел
# за размер комнаты. Оригинал — 6 обращений (ix+n) на вызов (85 % тактов процедуры уходило
# в хелперы il_ix_*). Здесь указатель объекта живёт в HL (dY/dZ вызывателя — на стеке),
# поля +1/+4 (+2/+5) обходятся inc/dec hl, редкая ветка OOB — единственный переход к +12.
# Проверки «входим в комнату» (+12) и «рядом с аркой» (+7) переставлены местами: обе —
# чистые чтения с ret nz, порядок не наблюдаем, зато +7 и +12 идут по одному указателю.
# У dY правимое смещение живёт в E (DE у вызывателя мертво), потому что L занят указателем.
# 1017 -> ~250 тактов на вызов.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# upd_player_bottom / upd_player_top — точки диспетчера для двух половин игрока.
# 76-80 % тактов этих процедур уходило в хелперы il_ix_* (5 и 8 обращений (ix/iy+n)).
# Здесь указатель объекта берётся в HL по месту; в upd_player_top после ldir DE уже
# указывает на top+8, поэтому +6/+7/+13 обходятся ex de,hl и inc/dec hl.
# ВАЖНО: смещения 39 и 45 (поля второй половины игрока) считаются 16-битно — младший
# байт объекта доходит до 232, и +39 переносит; ASSERT для l+7 здесь не применим.
# ---------------------------------------------------------------------------
N_UPD_PLAYER_BOTTOM = """upd_player_bottom:                                              ; dead?
				ld		hl, (z80_ix)
				ld		a, l
				add		a, 13
				ld		l, a
				ld		a, h
				adc		a, 0
				ld		h, a								; ix+13
				ld		a, (hl)
				and		0x40								; bit 6 — убит?
				jp		z, loc_C83E							; no, skip
				ld      a, (all_objs_in_cauldron)
				and     a
				jp		nz, loc_C83E
				ld		a, l
				add		a, 32
				ld		l, a
				ld		a, h
				adc		a, 0
				ld		h, a								; ix+45
				ld		a, (hl)
				or		0x40								; set dead top half
				ld		(hl), a
				jp      init_death_sparkles

loc_C83E:                                                       ; returns to caller if transforming
				call    chk_and_init_transform
				call    check_user_input
				call    handle_pickup_drop
				call    handle_left_right
				call    handle_jump
				call    handle_forward
				call    chk_plyr_OOB
				jp		nc, plyr_OOB

loc_C855:		ld		hl, (z80_ix)						; fudge near arch
				ld		a, l
				add		a, 39
				ld		l, a
				ld		a, h
				adc		a, 0
				ld		h, a								; ix+0x27
				ld		a, (hl)
				or		2									; set 1 — fudge
				ld		(hl), a
				call    move_player                             ; does the moving
				ld		hl, (z80_ix)
				ld		a, l
				add		a, 39
				ld		l, a
				ld		a, h
				adc		a, 0
				ld		h, a								; ix+0x27
				ld		a, (hl)
				and		0xFD								; res 1 — reset fudge
				ld		(hl), a
				ld		a, l
				sub		27
				ld		l, a
				ld		a, h
				sbc		a, 0
				ld		h, a								; ix+12
				ld		a, (hl)								; flags12
				sub     0x10								; entering room?
				jp		c, loc_C86A							; no, skip
				ld		(hl), a								; dec entering room counter

loc_C86A:		jp      set_wipe_and_draw_flags
; ---------------------------------------------------------------------------
; when walking through arches

plyr_OOB:		ld		hl, (z80_ix)						; dZ
				ld		a, l
				add		a, 11
				ld		l, a								; ix+11 (+11 не переносит: младший байт <= 232 -> 243)
				ld		a, (hl)
				and     a                                       ; <0?
				jp      m, loc_C855                             ; yes, go
				xor		a
				ld		(hl), a								; dZ=0
				jp		loc_C855
"""

N_UPD_PLAYER_TOP = """upd_player_top:
				ld      a, (all_objs_in_cauldron)
				and     a
				jp		nz, loc_CDEF
				ld		hl, (z80_ix)
				ld		a, l
				add		a, 13
				ld		l, a
				ld		a, h
				adc		a, 0
				ld		h, a								; ix+13
				ld		a, (hl)
				and		0x40								; bit 6 — убит?
				jp		nz, init_death_sparkles

loc_CDEF:		ld		hl, (z80_ix)						; DE = top
				ld		d, h
				ld		e, l
				ld		a, l
				sub		32
				ld		l, a
				ld		a, h
				sbc		a, 0
				ld		h, a								; HL = bottom = top-32
				ld		(z80_iy), hl						; iy = player bottom half
				inc		de									; DE = top+1
				inc		hl									; HL = bottom+1
				ld      bc, 7
				ldir                                            ; copy x,y,z,w,d,h,flags
				ex		de, hl								; HL = top+8
				dec		hl
				dec		hl									; top+6
				ld		(hl), 0								; height (top)
				inc		hl									; top+7
				ld		a, (hl)
				or		2									; ignore in 3D calcs
				ld		(hl), a
				inc		hl
				inc		hl
				inc		hl
				inc		hl
				inc		hl
				inc		hl									; top+13
				ld		a, (hl)								; flags13
				and     0xF                                    ; look around counter
				jp		z, loc_CE14                             ; maybe look around again?
				dec		(hl)								; dec look around counter
				jp		loc_CE27
; ---------------------------------------------------------------------------

loc_CE14:                                                       ; look in a random direction
				ld      a, (seed_3)
				cp      2                                      ; one way?
				jp		c, loc_CE33                            ; yes, go
				cp      0xFE                                   ; other way?
				jp		nc, loc_CE40                           ; yes, go
				ld		hl, (z80_iy)
				ld		a, (hl)								; straight ahead (iy+0)

set_top_sprite:	add     a, 16
				ld		hl, (z80_ix)
				ld		(hl), a								; graphic_no (top half)

loc_CE27:		ld		hl, (z80_iy)						; Z (bottom half)
				inc		hl
				inc		hl
				inc		hl									; iy+3
				ld		a, (hl)
				add     a, 12                                  ; directly above
				ld		hl, (z80_ix)
				inc		hl
				inc		hl
				inc		hl									; ix+3
				ld		(hl), a								; store in top half
				jp		set_draw_objs_overlapped
; ---------------------------------------------------------------------------

loc_CE33:		ld		hl, (z80_iy)						; graphic_no (bottom half)
				ld		a, (hl)
				and     0xF8
				or      6                                      ; look one way

loc_CE3A:		push	af									; look for 8 iterations
				ld		hl, (z80_ix)
				ld		a, l
				add		a, 13
				ld		l, a
				ld		a, h
				adc		a, 0
				ld		h, a								; ix+13
				ld		(hl), 8
				pop		af
				jp		set_top_sprite
; ---------------------------------------------------------------------------

loc_CE40:		ld		hl, (z80_iy)
				ld		a, (hl)
				and     0xF8
				or      7                                      ; look the other way
				jp		loc_CE3A
"""

# ---------------------------------------------------------------------------
# dec_dZ_and_update_XYZ / add_dXYZ: X += dX, Y += dY, Z += dZ. Девять обращений (ix+n)
# = 1180 тактов на вызов. Координата и смещение всегда стоят на 8 байт друг от друга,
# поэтому здесь два указателя: HL на +1..+3, DE на +9..+11, оба двигаются inc.
# Оригинал портит только A и флаги — BC/DE/HL уходят на стек. 1180 -> 190 тактов.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# upd_80_to_83 (призрак) и тройка toggle_next_prev_sprite / next_graphic_no_mod_4 /
# save_graphic_no: 80-89 % тактов уходило в хелперы il_ix_* при одном-двух обращениях
# к полю. Указатель объекта берётся в HL по месту; после get_delta_from_tbl (портит
# HL и BC) значение прячется в C — BC у вызывателя всё равно уже мертво.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Голова flip_sprite: одно обращение (ix+0) стоило ~200 тактов хелпера при 17 вызовах
# за итерацию. Хвост (flip_sprite_data) — в kl_native, не трогаем.
# ---------------------------------------------------------------------------
N_FLIP_HEAD = """flip_sprite:
				ld		hl, (z80_ix)
				ld		a, (hl)								; sprite index
				ld		l, a
				ld		h, 0
				add     hl, hl                                  ; word offset
				ld      bc, sprite_tbl
				add     hl, bc                                  ; sprite table entry
				ld      e, (hl)
				inc     hl
				ld      d, (hl)                                 ; DE = sprite address
				ld      a, (de)                                 ; width
				and     a                                       ; null sprite?
				jp      NZ, flip_sprite_data                    ; no, skip
				inc     sp
				inc     sp                                      ; exit from caller
				ret
"""

N_UPD_80_83 = """upd_80_to_83:
				call    adj_m6_m12
				call    dec_dZ_and_update_XYZ
				ld		hl, (z80_ix)
				ld		a, l
				add		a, 9
				ld		l, a								; ix+9
				ld		a, (hl)								; dX
				inc		hl									; ix+10
				or		(hl)								; moving X/Y?
				jp		z, loc_C5DD							; no, skip
				inc		hl
				inc		hl									; ix+12
				ld		a, (hl)
				and		3									; OOB X/Y?
				jp		z, loc_C5FD							; no, continue as-is

loc_C5DD:		ld      a, (seed_3)
				and     3
				add     a, 4                                   ; rnd(4-7)= +/-3or4
				call    get_delta_from_tbl
				ld		c, a
				ld		hl, (z80_ix)
				ld		a, l
				add		a, 9
				ld		l, a
				ld		(hl), c								; set dX
				ld      a, (seed_2)
				and     3
				add     a, 4                                   ; rnd(4-7)= +/-3or4
				call    get_delta_from_tbl
				ld		c, a
				ld		hl, (z80_ix)
				ld		a, l
				add		a, 10
				ld		l, a
				ld		(hl), c								; dY
				call    calc_ghost_sprite
				call    audio_B467

loc_C5FD:		call    toggle_next_prev_sprite
				jp      set_deadly_wipe_and_draw_flags
"""

N_TOGGLE_SPR = """toggle_next_prev_sprite:
				push	hl
				ld		hl, (z80_ix)
				ld		a, (hl)								; graphic_no
				xor		1									; toggle next/prev sprite
				ld		(hl), a
				pop		hl
				ret
; ---------------------------------------------------------------------------

next_graphic_no_mod_4:									; graphic no.
				push	hl
				ld		hl, (z80_ix)
				ld		a, (hl)
				ld		c, a
				and		0xFC								; mask off low 2 bits
				ld		b, a
				ld		a, c
				inc		a
				and		3									; inc low 2 bits
				or		b									; mask them back in
				ld		(hl), a
				pop		hl
				ret

save_graphic_no:										; store new graphic no
				push	hl
				ld		hl, (z80_ix)
				ld		(hl), a
				pop		hl
				ret
; End of function toggle_next_prev_sprite
"""

N_ADD_DXYZ = """dec_dZ_and_update_XYZ:
				push	hl
				ld		hl, (z80_ix)
				ld		a, l
				add		a, 11
				ld		l, a								; ix+11 (+11 не переносит: младший байт <= 232)
				dec		(hl)								; dec dZ
				pop		hl
				call    adj_for_out_of_bounds

add_dXYZ:		push	hl									; X, Y, Z += dX, dY, dZ
				push	de
				ld		hl, (z80_ix)
				inc		hl									; HL = ix+1 (X)
				ld		a, l
				add		a, 8
				ld		e, a
				ld		a, h
				adc		a, 0
				ld		d, a								; DE = ix+9 (dX)
				ld		a, (de)
				add		a, (hl)
				ld		(hl), a								; X += dX
				inc		hl
				inc		de
				ld		a, (de)
				add		a, (hl)
				ld		(hl), a								; Y += dY
				inc		hl
				inc		de
				ld		a, (de)
				add		a, (hl)
				ld		(hl), a								; Z += dZ
				pop		de
				pop		hl
				ret
; End of function dec_dZ_and_update_XYZ
"""

N_ADJ_DX_OOB = """adj_dX_for_out_of_bounds:	; C = dX; прижать dX к границам комнаты по X
				push	hl									; dY/dZ вызывателя
				ld		hl, (z80_ix)
				ld		a, l
				add		a, 7
				ld		l, a								; ix+7
				ld		a, (hl)
				and		1									; near an arch?
				jp		nz, adxo_out
				inc		hl
				inc		hl
				inc		hl
				inc		hl
				inc		hl									; ix+12
				ld		a, (hl)
				and		0xF0								; entering room?
				jp		nz, adxo_out
				ld		a, (room_size_X)
				ld		b, a
				ld		hl, (z80_ix)
				inc		hl									; ix+1
adxo_lp:		ld		a, (hl)								; X
				add		a, c								; +dX
				sub		128
				jp		nc, adxo_1
				cpl
				inc		a									; neg
adxo_1:			inc		hl
				inc		hl
				inc		hl									; ix+4
				add		a, (hl)								; +width
				dec		hl
				dec		hl
				dec		hl									; обратно ix+1 (dec hl флаги не портит)
				cp		b									; < room width?
				jp		c, adxo_out							; да, готово
				push	hl
				ld		a, l
				add		a, 11
				ld		l, a
				ld		a, h
				adc		a, 0
				ld		h, a								; ix+12
				ld		a, (hl)
				or		1									; set X OOB
				ld		(hl), a
				pop		hl
				ld		a, c								; dX
				call    adj_d_for_out_of_bounds
				ld		c, a								; new dX
				jp		nz, adxo_lp							; check again
adxo_out:		pop		hl
				ret
; End of function adj_dX_for_out_of_bounds
"""

N_ADJ_DY_OOB = """adj_dY_for_out_of_bounds:	; L = dY; прижать dY к границам комнаты по Y
				ld		e, l								; E = dY (DE у вызывателя мертво)
				push	hl									; dY/dZ вызывателя
				ld		hl, (z80_ix)
				ld		a, l
				add		a, 7
				ld		l, a								; ix+7
				ld		a, (hl)
				and		1									; near an arch?
				jp		nz, adyo_out
				inc		hl
				inc		hl
				inc		hl
				inc		hl
				inc		hl									; ix+12
				ld		a, (hl)
				and		0xF0								; entering room?
				jp		nz, adyo_out
				ld		a, (room_size_Y)
				ld		b, a
				ld		hl, (z80_ix)
				inc		hl
				inc		hl									; ix+2
adyo_lp:		ld		a, (hl)								; Y
				add		a, e								; +dY
				sub		128
				jp		nc, adyo_1
				cpl
				inc		a									; neg
adyo_1:			inc		hl
				inc		hl
				inc		hl									; ix+5
				add		a, (hl)								; +depth
				dec		hl
				dec		hl
				dec		hl									; обратно ix+2
				cp		b									; < room depth?
				jp		c, adyo_out							; да, готово
				push	hl
				ld		a, l
				add		a, 10
				ld		l, a
				ld		a, h
				adc		a, 0
				ld		h, a								; ix+12
				ld		a, (hl)
				or		2									; set Y OOB
				ld		(hl), a
				pop		hl
				ld		a, e								; dY
				call    adj_d_for_out_of_bounds
				ld		e, a								; new dY
				jp		nz, adyo_lp							; check again
adyo_out:		pop		hl
				ld		l, e								; новый dY — на место
				ret
; End of function adj_dY_for_out_of_bounds
"""

N_ADJ_OOB = """adj_for_out_of_bounds:	; прижать dX/dY/dZ (C/L/H) к границам комнаты и к другим объектам
				ld		hl, (z80_ix)
				ld		a, l
				add		a, 7
				ld		l, a								; ix+7
				ld		a, (hl)
				and		2									; уже внутри?
				ret		nz
				ld		a, (hl)
				or		2
				ld		(hl), a								; set 1,(ix+7)
				ld		a, l
				add		a, 5
				ld		l, a								; ix+12
				ld		a, (hl)
				and		0xF8								; сбросить флаги OOB по X,Y,Z
				ld		(hl), a
				dec		hl									; ix+11
				ld		a, (hl)								; dZ
				and		a									; ноль?
				ld		h, a								; H = dZ
				ld		l, 0								; L = dY = 0
				ld		c, l								; C = dX = 0
				jp		z, dZ_ok
				call	adj_dZ_for_out_of_bounds
				ld		a, h
				and		a
				jp		z, dZ_ok
				call	adj_dZ_for_obj_intersect

dZ_ok:			ex		de, hl								; dY/dZ спрятать в DE
				ld		hl, (z80_ix)
				ld		a, l
				add		a, 9
				ld		l, a								; ix+9
				ld		a, (hl)								; dX
				ex		de, hl
				and		a									; ноль?
				ld		c, a
				jp		z, loc_CB7B
				call	adj_dX_for_out_of_bounds
				ld		a, c
				and		a
				jp		z, loc_CB7B
				call	adj_dX_for_obj_intersect

loc_CB7B:		ex		de, hl
				ld		hl, (z80_ix)
				ld		a, l
				add		a, 10
				ld		l, a								; ix+10
				ld		a, (hl)								; dY
				ex		de, hl
				and		a									; ноль?
				ld		l, a
				jp		z, loc_CB8C
				call	adj_dY_for_out_of_bounds
				ld		a, l
				and		a
				jp		z, loc_CB8C
				call	adj_dY_for_obj_intersect

loc_CB8C:		ex		de, hl								; DE = dY(E)/dZ(D)
				ld		hl, (z80_ix)
				ld		a, l
				add		a, 9
				ld		l, a								; ix+9
				ld		(hl), c								; новый dX
				inc		hl
				ld		(hl), e								; новый dY
				inc		hl
				ld		(hl), d								; новый dZ
				ld		a, l
				sub		4
				ld		l, a								; ix+7
				ld		a, (hl)
				and		0xFD								; res 1,(ix+7)
				ld		(hl), a
				ex		de, hl								; вернуть HL = dY/dZ, как у оригинала
				ret
; End of function adj_for_out_of_bounds
"""


# ---------------------------------------------------------------------------
# move_player, calc_2d_info, set_wipe_and_draw_flags, handle_exit_screen — те же
# указатели по HL вместо (ix+n). В move_player важно, что `bit 3,c` у Z80 не портит A:
# на i8080 dZ перечитывается из (hl) после and (MOV A,M флаги не трогает).
# ---------------------------------------------------------------------------
N_MOVE_PLAYER = """move_player:
				ld		a, (obj_dropping_into_cauldron)
				and		a
				jp		z, loc_C9AB
				ld		hl, (z80_ix)
				ld		a, l
				add		a, 11
				ld		l, a
				ld		(hl), 2								; dZ = 2

loc_C9AB:		ld		hl, (z80_ix)
				ld		a, l
				add		a, 12
				ld		l, a								; ix+12
				ld		a, (hl)
				and		8									; уже прыгает?
				jp		nz, loc_C9BC
				ld		a, (hl)								; flags12
				and		0xF0								; входит в комнату?
				jp		nz, loc_C9BC
				ld		a, c
				and		4									; вперёд?
				jp		z, loc_C9C1

loc_C9BC:		push	bc
				call	calc_plyr_dXY
				pop		bc

loc_C9C1:		ld		hl, (z80_ix)
				ld		a, l
				add		a, 11
				ld		l, a								; ix+11
				ld		a, (hl)								; dZ
				and		a
				jp		m, loc_C9CC
				ld		a, c
				and		8									; прыжок?
				ld		a, (hl)								; вернуть dZ, флаги от and целы
				jp		nz, loc_C9CD

loc_C9CC:		dec		a

loc_C9CD:		dec		a
				ld		(hl), a								; dZ
				ld		(tmp_dZ), a
				add		a, 2
				call	m, audio_B451
				call	adj_for_out_of_bounds
				call	handle_exit_screen
				call	add_dXYZ
				ld		hl, (z80_ix)
				ld		a, l
				add		a, 12
				ld		l, a								; ix+12
				ld		a, (hl)
				and		4									; Z вышел за границу?
				jp		z, clear_dX_dY
				ld		a, (tmp_dZ)
				and		a
				jp		p, clear_dX_dY
				ld		a, (hl)
				and		0xF7								; res 3 — снять флаг прыжка
				ld		(hl), a

"""

N_CALC_2D = """calc_2d_info:	; obj[24] = ширина спрайта в байтах (+1, если pixelX не кратен 8), obj[25] = высота
				call	calc_pixel_XY
				call	sprite_addr							; DE -> данные спрайта (без разворота: w/h от него не зависят)
				ld		hl, (z80_ix)
				ld		a, l
				add		a, 24
				ld		l, a
				ld		a, h
				adc		a, 0
				ld		h, a								; ix+24
				inc		hl
				inc		hl									; ix+26
				ld		a, (hl)								; pixel X
				and		7									; смещение в бите
				ld		a, (de)								; ширина (байт)
				inc		de
				jp		z, c2d_1
				inc		a
c2d_1:			and		0xF
				dec		hl
				dec		hl									; ix+24
				ld		(hl), a
				inc		hl									; ix+25
				ld		a, (de)
				ld		(hl), a								; высота
				ret
; End of function calc_2d_info
"""

N_SET_WD = """set_wipe_and_draw_flags:
				ld		hl, (z80_ix)
				ld		a, l
				add		a, 7
				ld		l, a
				ld		a, (hl)
				or		0x30								; set WIPE & DRAW
				ld		(hl), a
				jp		set_draw_objs_overlapped
; End of function set_wipe_and_draw_flags
"""

N_HANDLE_EXIT = """handle_exit_screen:
				ld		hl, (z80_ix)
				ld		a, l
				add		a, 12
				ld		l, a								; ix+12
				ld		a, (hl)							; flags12
				and		0xF0								; входит в комнату?
				ret		nz									; да, выходим
				ld		a, l
				sub		5
				ld		l, a								; ix+7
				ld		a, (hl)
				and		1									; рядом с аркой?
				ret		z									; нет, выходим
				ld		a, (hl)
				and		0xFE								; res 0 — снять флаг
				ld		(hl), a
				ld		bc, screen_move_tbl
				ld		hl, (room_size_X)
				push	hl
				jp		lookup_plyr_dXY
; End of function handle_exit_screen
"""


# ---------------------------------------------------------------------------
# list_objects_to_draw: тот же алгоритм, что в kl_native, но поле флагов читается
# сдвигом L на +7 без adc и без push/pop, а возврат к базе слит с шагом +32 (+25).
# 118 -> 62 такта на пустой объект, 101-117 на непустой.
# ---------------------------------------------------------------------------
N_ZERO_END = """zero_end_of_graphic_objs_tbl:	; DE = первый свободный слот таблицы объектов
				; Загрузчик комнаты (retrieve_screen) кладёт объекты В ПОДРЯД, начиная с
				; other_objs_here, и здесь обнуляет ВЕСЬ хвост до font. Значит DE в этой точке —
				; граница «занято / пусто», и она же верхняя граница всех живых объектов до
				; следующей загрузки: позже graphic_no пишется только в СВОЙ объект диспетчера
				; (ix) и в спецобъекты 2/3, то есть в слоты ниже границы.
				push	de
				ld		hl, graphic_objs_tbl
				ld		b, 0
				ld		c, 40								; страховка: не найдём DE — останется 40
zeg_cnt:		ld		a, l
				cp		e
				jp		nz, zeg_nx
				ld		a, h
				cp		d
				jp		z, zeg_got
zeg_nx:			ld		a, l
				add		a, 32
				ld		l, a
				jp		nc, zeg_n2
				inc		h
zeg_n2:			inc		b
				dec		c
				jp		nz, zeg_cnt
				ld		b, 40								; страховка: DE не нашлось — сканировать всё
zeg_got:		ld		a, b
				ld		(kl_nobj), a						; для отладки/проверки инварианта
				ld		(sd_nb+1), a
				ld		(adx_nb+1), a
				ld		(ady_nb+1), a
				ld		(adz_nb+1), a
				ld		(dai_nb+1), a
				ld		(lo_nb+1), a
				pop		de

zeg_lp:			ld      hl, clear_end					; конец таблицы объектов (в оригинале метка font)
				and     a
				sbc     hl, de                                  ; done?
				ret     Z                                       ; yes, exit
				ld      b, 32                                  ; 32 bytes to clear
				call    zero_DE
				jp      zeg_lp
"""


N_LIST_OBJS = """list_objects_to_draw:	; индексы объектов с graphic_no != 0 и DRAW (бит 4 поля 7) -> objects_to_draw, терминатор 0xFF
				ld		hl, graphic_objs_tbl
				ld		de, objects_to_draw
				ld		c, 0
lo_nb:			ld		b, 40								; иммедиат: занятые записи таблицы (правит zeg_got)
lo_1:			ld		a, (hl)
				or		a
				jp		z, lo_2
				ld		a, l
				add		a, 7
				ld		l, a								; -> поле флагов (+7 не переносит)
				ld		a, (hl)
				and		0x10								; DRAW?
				jp		z, lo_1b
				ld		a, c
				ld		(de), a
				inc		de
lo_1b:			ld		a, l								; -7 и +32 одним сложением
				add		a, 25
				ld		l, a
				jp		nc, lo_3
				inc		h
				jp		lo_3
lo_2:			ld		a, l
				add		a, 32
				ld		l, a
				jp		nc, lo_3
				inc		h
lo_3:			inc		c
				dec		b
				jp		nz, lo_1
				ld		a, 0xFF
				ld		(de), a
				ret
; End of function list_objects_to_draw
"""


# ---------------------------------------------------------------------------
# wipe_next_object: стереть в буфере объединённый прямоугольник старого и нового
# положения каждого объекта из списка отрисовки. 16 обращений (ix+n) на объект
# (~1960 тактов на вызов). Здесь адрес объекта, который и так возвращает
# get_ptr_object, остаётся в HL и служит указателем: все поля 24..31 читаются
# одним проходом inc/dec hl. Результаты (ширина/высота) копятся в kl_wl/kl_wh и
# берутся в HL одним ld hl,(kl_wl) перед screen_calc_addr_de.
# Три push подряд перед jp wipe_next_object — как в оригинале (jonsole их не снимает;
# стек сбрасывает ld sp,seed_1 в update_sprite_loop следующего кадра).
# ---------------------------------------------------------------------------
N_WIPE = """wipe_next_object:
				ld		hl, (tmp_objects_to_draw)
				ld		a, (hl)							; индекс объекта
				inc		hl
				ld		(tmp_objects_to_draw), hl
				cp		0xFF								; конец списка?
				jp		z, loc_D653
				call	get_ptr_object					; HL = адрес объекта
				ld		(z80_ix), hl
				ld		a, l
				add		a, 7
				ld		l, a								; obj+7
				ld		a, (hl)
				and		0x20								; флаг WIPE?
				jp		z, wipe_next_object
				ld		a, (hl)
				and		0xDF								; снять флаг WIPE
				ld		(hl), a
				ld		a, l
				add		a, 19
				ld		l, a
				ld		a, h
				adc		a, 0
				ld		h, a								; obj+26 (pixel X)
				ld		a, (hl)
				ld		c, a								; C = pixel X
				inc		hl
				inc		hl
				inc		hl
				inc		hl								; obj+30 (старый pixel X)
				ld		a, c
				sub		(hl)
				jp		c, wno_x1						; новый левее — C уже = pixel X
				ld		c, (hl)							; иначе C = старый pixel X
wno_x1:			ld		a, (hl)							; старый pixel X
				rrca
				rrca
				rrca
				and		0x1F
				dec		hl
				dec		hl								; obj+28 (старая ширина)
				add		a, (hl)
				ld		e, a								; правый край старого
				dec		hl
				dec		hl								; obj+26
				ld		a, (hl)
				rrca
				rrca
				rrca
				and		0x1F
				dec		hl
				dec		hl								; obj+24 (ширина)
				add		a, (hl)
				cp		e
				jp		c, wno_x2
				ld		e, a								; правый край = максимум
wno_x2:			ld		a, c
				rrca
				rrca
				rrca
				and		0x1F
				ld		b, a								; левый байт
				ld		a, e
				sub		b
				ld		(kl_wl), a						; сколько байт стирать
				inc		hl
				inc		hl
				inc		hl								; obj+27 (pixel Y)
				ld		a, (hl)
				ld		b, a								; B = pixel Y
				ld		a, l
				add		a, 4
				ld		l, a								; obj+31 (старый pixel Y)
				ld		a, b
				sub		(hl)
				jp		c, wno_y1						; новый ниже — B уже = pixel Y
				ld		b, (hl)							; иначе B = старый pixel Y
wno_y1:			ld		a, (hl)							; старый pixel Y
				dec		hl
				dec		hl								; obj+29 (старая высота)
				add		a, (hl)
				ld		e, a
				dec		hl
				dec		hl								; obj+27
				ld		a, (hl)
				dec		hl
				dec		hl								; obj+25 (высота)
				add		a, (hl)
				cp		e
				jp		nc, wno_y2
				ld		a, e
wno_y2:			sub		b
				ld		(kl_wh), a						; сколько строк стирать
				ld		a, b
				cp		192								; ниже экрана?
				jp		nc, wipe_next_object			; да, дальше
				ld		hl, kl_wh
				add		a, (hl)
				sub		192
				jp		c, wno_go
				cpl
				inc		a								; neg
				add		a, (hl)
				ld		(hl), a							; обрезать высоту по низу экрана
wno_go:			ld		hl, (kl_wl)						; L = ширина, H = высота
loc_D62C:		call	screen_calc_addr_de				; DE = адрес экрана
				push	de
				ex		de, hl
				call	vidbuf_calc_addr_in_hl			; HL = адрес в буфере
				push	hl
				push	de
				ld		b, d
				call	vidbuf_clear_window
				ld		hl, objs_wiped_cnt
				inc		(hl)
				jp		wipe_next_object

"""

# ---------------------------------------------------------------------------
# set_pixel_adj (был нативным в kl_native): obj[18] = L, obj[19] = H, A/F/HL/DE целы.
# 21 вызов на итерацию, поэтому важен каждый такт: HL восстанавливается вторым ex de,hl
# (после первого DE = значение, HL свободен под указатель), push/pop hl не нужны;
# смещение 18 не переносит младший байт объекта (<= 232+18 = 250), значит без adc.
# 140 -> 112 тактов.
# ---------------------------------------------------------------------------
N_SET_PIXEL_ADJ = """set_pixel_adj:	; obj[18] = L, obj[19] = H. AF/HL/DE сохраняются (оригинал не трогает A).
				push	af
				push	de
				ex		de, hl								; DE = значение, HL свободен
				ld		hl, (z80_ix)
				ld		a, l
				add		a, 18
				ld		l, a
				ld		(hl), e
				inc		hl
				ld		(hl), d
				ex		de, hl								; HL обратно = значение
				pop		de
				pop		af
				ret
; End of function set_pixel_adj
"""

# ---------------------------------------------------------------------------
# is_object_not_ignored: Z = объект пуст или помечен «игнорировать в 3D» (бит 1 поля 7).
# Оригинал портит только A и флаги.
# ---------------------------------------------------------------------------
N_IS_NOT_IGNORED = """is_object_not_ignored:
				push	hl
				ld		hl, (z80_iy)
				ld		a, (hl)							; graphic no
				and		a									; null?
				jp		z, ioni_out						; да, выходим
				ld		a, l
				add		a, 7
				ld		l, a
				ld		a, (hl)							; flags
				cpl
				and		2									; бит 1 (ignore)
ioni_out:		pop		hl
				ret
; End of function is_object_not_ignored
"""

# ---------------------------------------------------------------------------
# get_ptr_object: HL = graphic_objs_tbl + 32*(A & 0x7F).
# Оригинал: HL = A, пять add hl,hl, ld bc,base, add hl,bc, push/pop bc — 120 тактов.
# Здесь три rrca разводят индекс на «страницу» и «смещение» сразу: после rrca*3
# младшие 5 бит A = индекс>>3, старшие 3 бита = (индекс&7)<<5 (нужно индекс <= 0x7F).
# Младший байт базы (0xC868) не помещается в 5 бит, поэтому его старшие биты
# (KL_GPO_K = 3) вносятся в индекс: base + 32*i = KL_GPO_PG*256 + 32*(i+K) + (base&31),
# и тогда (A&0xE0) + (base&31) <= 0xE0+24 не переносит. Индексы объектов 0..39,
# +K не выводит за 0x7F (ASSERT).
# BC/DE сохраняются, как в оригинале. 77 тактов, 17 байт (было 120 тактов, 17 байт).
# ---------------------------------------------------------------------------
N_GET_PTR_OBJ = """get_ptr_object:
				ASSERT (graphic_objs_tbl & 31) <= 24		; иначе (A&0xE0)+(base&31) переносит
				ASSERT KL_GPO_K + 40 <= 0x7F				; индекс объекта 0..39 плюс K не выходит за 7 бит
				and		0x7F
				add		a, KL_GPO_K
				rrca
				rrca
				rrca
				ld		l, a
				and		0x1F								; (индекс+K)>>3 = номер страницы
				add		a, KL_GPO_PG
				ld		h, a
				ld		a, l
				and		0xE0								; ((индекс+K)&7)*32
				add		a, graphic_objs_tbl & 31
				ld		l, a
				ret
; End of function get_ptr_object
"""


# ---------------------------------------------------------------------------
# calc_display_order_and_render, тело: перебор пар объектов из objects_to_draw
# с вычислением класса перекрытия. Самое дорогое место тяжёлых комнат:
# 124 класса/итерацию по 939 тактов (копии полей в kl_o1/kl_o2 + jump_to_tbl_entry).
#
# Что сделано:
#  * КЛАСС НЕ ВЫЧИСЛЯЕТСЯ ЦЕЛИКОМ. Таблица off_CF69 отдаёт «продолжить цикл» для всех
#    классов, кроме 3,4,6,7,12,15,16 (-> d_3467121516) и 13 (-> objs_coincide).
#    В коде c = z + 3*y + 9*x, z/y/x из {0,1,2}; интересны ровно z!=2, y!=0, x!=2,
#    а класс 13 — это (x=1,y=1,z=1). Значит достаточно дерева ветвлений:
#    y (первым, потому что y=0 — единственный выход по ПЕРВОМУ сравнению оси), затем x,
#    затем z; таблица переходов и jump_to_tbl_entry не нужны вовсе.
#    Соответствие проверено по всем 27 входам таблицы, судья — локстеп.
#  * ГРАНИЦЫ ОБЪЕКТА 1 (Z1, Z1+H1, Y1±D1, X1±W1) считаются один раз на объект 1
#    (ov_prep, ~23 раза за итерацию) и живут ИММЕДИАТАМИ внутри сравнений — сравнение
#    с полем объекта 2 стоит 7 тактов и не занимает регистров.
#  * ОБЪЕКТ 2 читается указателем HL по месту (inc l / dec l, младший байт не переносит:
#    (graphic_objs_tbl & 31) = 8, +6 <= 0xEE), копий в память нет.
#  * «Тот же объект?» — сравнение ИНДЕКСОВ (иммедиат ov_i1) до вызова get_ptr_object,
#    а не 16-битное вычитание указателей после (get_ptr_object чист, порядок не виден).
#  * render_obj_2 сохраняется не на каждый объект 2, а только в d_3467121516: до него
#    DE = тот самый указатель списка (класс перекрытия DE не трогает).
#  * bit 7 -> or a / jp m; set 7,(hl) -> ld a,(hl)/or 0x80/ld (hl),a без push af.
# Побочные эффекты сохранены: objs_coincide правит только поле 0 (иммедиаты границ живы),
# render_list/rendered_objs_cnt/render_obj_1 — как в оригинале.
# ---------------------------------------------------------------------------
N_DISPLAY_ORDER = """KL_GPO_K		equ		(graphic_objs_tbl >> 5) & 7	; get_ptr_object: старшие биты младшего байта базы уходят в индекс

KL_GPO_PG		equ		(graphic_objs_tbl - 32*KL_GPO_K - (graphic_objs_tbl & 31)) >> 8
calc_display_order_and_render:
				ASSERT (graphic_objs_tbl & 31) <= 25		; иначе l+6 переносит и чтение полей объекта 2 ломается
				xor		a
				ld		(rendered_objs_cnt), a
				ld		hl, (z80_ix)						; push ix
				push	hl
				ld		hl, (z80_iy)						; push iy
				push	hl
				ld		hl, objects_to_draw
				ld		(kl_scan), hl						; отсюда начинается обход списка

process_remaining_objs:
				ld		de, (kl_scan)
loc_CEC6:		ld		a, (de)								; graphic no
				inc		de
				cp		0xFF								; конец списка?
				jp		z, render_done
				or		a
				jp		m, loc_CEC6							; бит 7 = уже отрисован
				ld		(ov_i1+1), a						; индекс объекта 1 -> иммедиат сравнения
				ld		h, d
				ld		l, e
				ld		(render_obj_1), hl
				dec		hl
				ld		(kl_scan), hl						; первая ненарисованная запись: флаги только ставятся
				call	get_ptr_object
				ld		(z80_ix), hl
				call	ov_prep								; шесть границ объекта 1 -> иммедиаты
loc_CEDB:		ld		a, (de)								; следующий graphic no
				inc		de
				or		a									; бит 7 чист -> объект годится (частый случай: 26 тактов)
				jp		p, ov_i1
				cp		0xFF								; конец списка?
				jp		z, render_obj_no1
				jp		loc_CEDB							; уже отрисован
ov_i1:			cp		0									; тот же объект? (иммедиат = индекс объекта 1)
				jp		z, loc_CEDB
				add		a, a								; индекс -> адрес объекта 2 + 2 по таблице
				ld		l, a
				ld		h, KL_OPTR_PG
				ld		a, (hl)
				inc		l
				ld		h, (hl)
				ld		l, a								; HL = объект 2 + 2 (поле Y)
				ld		a, (hl)
				ld		b, a
				inc		l
				inc		l
				inc		l									; +5 = D2
				add		a, (hl)								; Y2+D2
				ld		c, a
ov_ym:			ld		a, 0								; [Y1-D1]
				sub		c
				jp		nc, loc_CEDB						; y=0 -> любой класс продолжает цикл
				ld		a, b
				sub		(hl)								; Y2-D2
ov_yp:			sub		0									; [Y1+D1]
				jp		c, ov_y1							; y=1
				; y=2: класс интересен только при x!=2 и z!=2, и это всегда d_3467121516
				dec		l
				dec		l
				dec		l
				dec		l									; +1 = X2
				ld		a, (hl)
				ld		b, a
				inc		l
				inc		l
				inc		l									; +4 = W2
				add		a, (hl)								; X2+W2
				ld		c, a
ov_xm2:			ld		a, 0								; [X1-W1]
				sub		c
				jp		nc, ov_zs							; x=0
				ld		a, b
				sub		(hl)								; X2-W2
ov_xp2:			sub		0									; [X1+W1]
				jp		nc, loc_CEDB						; x=2 -> продолжить цикл
ov_zs:			dec		l									; +3 = Z2
				ld		a, (hl)
				ld		b, a
				inc		l
				inc		l
				inc		l									; +6 = H2
				add		a, (hl)								; Z2+H2
				ld		c, a
ov_z1s:			ld		a, 0								; [Z1]
				sub		c
				jp		nc, d_3467121516					; z=0
				ld		a, b								; Z2
ov_zhs:			sub		0									; [Z1+H1]
				jp		nc, loc_CEDB						; z=2 -> продолжить цикл
				jp		d_3467121516						; z=1
ov_y1:			dec		l
				dec		l
				dec		l
				dec		l									; +1 = X2
				ld		a, (hl)
				ld		b, a
				inc		l
				inc		l
				inc		l									; +4 = W2
				add		a, (hl)
				ld		c, a
ov_xm1:			ld		a, 0								; [X1-W1]
				sub		c
				jp		nc, ov_zs							; x=0 -> класс 3/4 (z!=2) = d_3467121516
				ld		a, b
				sub		(hl)
ov_xp1:			sub		0									; [X1+W1]
				jp		nc, loc_CEDB						; x=2
				dec		l									; x=1, y=1: +3 = Z2
				ld		a, (hl)
				ld		b, a
				inc		l
				inc		l
				inc		l									; +6 = H2
				add		a, (hl)
				ld		c, a
ov_z1f:			ld		a, 0								; [Z1]
				sub		c
				jp		nc, d_3467121516					; z=0 -> класс 12
				ld		a, b
ov_zhf:			sub		0									; [Z1+H1]
				jp		nc, loc_CEDB						; z=2 -> класс 14
				; z=1 -> класс 13: объекты совпали
				ld		a, l								; сюда всегда с L = объект 2 + 6
				sub		6
				ld		l, a
				ld		(z80_iy), hl
				ld		hl, (z80_ix)						; graphic no объекта 1
				ld		a, (hl)
				sub		0x60
				cp		7									; спецобъект?
				jp		nc, loc_CFF0
				ld		(hl), 187							; спрайт «искры»
				jp		loc_CEDB
loc_CFF0:		ld		hl, (z80_iy)						; graphic no объекта 2
				ld		a, (hl)
				sub		0x60
				cp		7
				jp		nc, loc_CEDB
				ld		(hl), 187
				jp		loc_CEDB
; ---------------------------------------------------------------------------

d_3467121516:	ld		a, l								; сюда всегда с L = объект 2 + 6
				sub		6
				ld		l, a
				ld		(z80_iy), hl						; объект 2 понадобится в loc_CFB8/loc_CFCE
				ld		h, d								; DE = render_obj_2 (класс перекрытия DE не трогает)
				ld		l, e
				ld		(render_obj_2), hl
				dec		hl									; -> запись объекта 2
				ld		c, (hl)								; индекс
				ld		de, render_list
loc_CFAD:		ld		a, (de)
				cp		0xFF								; пустая запись?
				jp		z, loc_CFB8
				cp		c									; уже в списке?
				jp		z, loc_CFCE
				inc		de
				jp		loc_CFAD
; ---------------------------------------------------------------------------

loc_CFB8:		ld		a, c
				ld		(de), a								; добавить в список
				inc		de
				ld		a, 0xFF
				ld		(de), a								; конец списка
				ld		a, c
				ld		(ov_i1+1), a						; объект 2 становится объектом 1
				ld		hl, (z80_iy)
				ld		(z80_ix), hl
				call	ov_prep
				ld		hl, (render_obj_2)
				ld		(render_obj_1), hl
				ld		de, (kl_scan)						; записи до kl_scan уже отрисованы
				jp		loc_CEDB
; ---------------------------------------------------------------------------

loc_CFCE:		ld		hl, (kl_scan)
loc_CFD1:		ld		a, (hl)								; graphic no
				inc		hl
				cp		0xFF								; конец списка?
				jp		z, process_remaining_objs
				cp		c									; тот, который ищем?
				jp		nz, loc_CFD1
				push	hl
				ld		hl, (z80_iy)
				ld		(z80_ix), hl						; объект 2 становится объектом 1
				pop		hl
				jp		render_obj
; ---------------------------------------------------------------------------

render_obj_no1:	ld		hl, (render_obj_1)
render_obj:		dec		hl									; -> запись объекта в objects_to_draw
				ld		a, (hl)
				or		0x80								; пометить отрисованным
				ld		(hl), a
				ld		a, 0xFF
				ld		(render_list), a
				ld		hl, rendered_objs_cnt
				inc		(hl)
				call	calc_pixel_XY_and_render
				jp		process_remaining_objs
; ---------------------------------------------------------------------------

ov_prep:		; HL = указатель объекта 1: границы по X/Y/Z -> иммедиаты класса перекрытия. Портит A, B, HL.
				inc		l									; +1 = X1
				ld		a, (hl)
				ld		b, a
				inc		l
				inc		l
				inc		l									; +4 = W1
				add		a, (hl)
				ld		(ov_xp1+1), a						; X1+W1
				ld		(ov_xp2+1), a
				ld		a, b
				sub		(hl)
				ld		(ov_xm1+1), a						; X1-W1
				ld		(ov_xm2+1), a
				dec		l
				dec		l									; +2 = Y1
				ld		a, (hl)
				ld		b, a
				inc		l
				inc		l
				inc		l									; +5 = D1
				add		a, (hl)
				ld		(ov_yp+1), a						; Y1+D1
				ld		a, b
				sub		(hl)
				ld		(ov_ym+1), a						; Y1-D1
				dec		l
				dec		l									; +3 = Z1
				ld		a, (hl)
				ld		(ov_z1s+1), a						; Z1
				ld		(ov_z1f+1), a
				inc		l
				inc		l
				inc		l									; +6 = H1
				add		a, (hl)
				ld		(ov_zhs+1), a						; Z1+H1
				ld		(ov_zhf+1), a
				ret
; ---------------------------------------------------------------------------

render_done:	pop		hl									; восстановить IY, IX вызывателя
				ld		(z80_iy), hl
				pop		hl
				ld		(z80_ix), hl
				ret
				ALIGN	256
kl_optr:	; индекс объекта (0..39) -> адрес его поля +2 (Y). Таблица СТАТИЧЕСКАЯ: адрес объекта
			; = graphic_objs_tbl + 32*i, ничего поддерживать не нужно. Выравнивание на страницу
			; экономит add a,low kl_optr (7 тактов) на каждой паре объектов внутренного цикла.
				DW		graphic_objs_tbl+2+32*0, graphic_objs_tbl+2+32*1, graphic_objs_tbl+2+32*2, graphic_objs_tbl+2+32*3
				DW		graphic_objs_tbl+2+32*4, graphic_objs_tbl+2+32*5, graphic_objs_tbl+2+32*6, graphic_objs_tbl+2+32*7
				DW		graphic_objs_tbl+2+32*8, graphic_objs_tbl+2+32*9, graphic_objs_tbl+2+32*10, graphic_objs_tbl+2+32*11
				DW		graphic_objs_tbl+2+32*12, graphic_objs_tbl+2+32*13, graphic_objs_tbl+2+32*14, graphic_objs_tbl+2+32*15
				DW		graphic_objs_tbl+2+32*16, graphic_objs_tbl+2+32*17, graphic_objs_tbl+2+32*18, graphic_objs_tbl+2+32*19
				DW		graphic_objs_tbl+2+32*20, graphic_objs_tbl+2+32*21, graphic_objs_tbl+2+32*22, graphic_objs_tbl+2+32*23
				DW		graphic_objs_tbl+2+32*24, graphic_objs_tbl+2+32*25, graphic_objs_tbl+2+32*26, graphic_objs_tbl+2+32*27
				DW		graphic_objs_tbl+2+32*28, graphic_objs_tbl+2+32*29, graphic_objs_tbl+2+32*30, graphic_objs_tbl+2+32*31
				DW		graphic_objs_tbl+2+32*32, graphic_objs_tbl+2+32*33, graphic_objs_tbl+2+32*34, graphic_objs_tbl+2+32*35
				DW		graphic_objs_tbl+2+32*36, graphic_objs_tbl+2+32*37, graphic_objs_tbl+2+32*38, graphic_objs_tbl+2+32*39
KL_OPTR_PG		equ		kl_optr >> 8
				ASSERT	(kl_optr & 0xFF) == 0
; End of function calc_display_order_and_render
"""

# ---------------------------------------------------------------------------
# calc_pixel_XY_and_render: graphic_no == 1 -> обнулить и выйти; иначе снять флаг DRAW,
# посчитать экранные X/Y и (при CY) провалиться в print_sprite.
# Оригинал в трансляции — три обращения (ix+n) через хелперы: 566 тактов на вызов.
# Здесь указатель объекта в HL; A и BC/DE после calc_pixel_XY те же, что были
# (calc_pixel_XY их портит одинаково в обоих вариантах), поэтому print_sprite
# получает тот же вход.
# ---------------------------------------------------------------------------
N_CALC_PXY_RENDER = """calc_pixel_XY_and_render:
				ASSERT (graphic_objs_tbl & 31) <= 24		; иначе l+7 переносит
				ld		hl, (z80_ix)
				ld		a, (hl)								; graphic no
				cp		1									; помечен «убрать»?
				jp		nz, loc_D710
				ld		(hl), 0								; да: обнулить и выйти
				ret
; ---------------------------------------------------------------------------

loc_D710:		ld		a, l
				add		a, 7
				ld		l, a
				ld		a, (hl)
				and		0xEF								; снять флаг DRAW
				ld		(hl), a
				call	calc_pixel_XY
				ret		nc									; ушёл за низ экрана — не рисовать
; End of function calc_pixel_XY_and_render
"""


# ---------------------------------------------------------------------------
# set_both_deadly_flags: (ix+13) |= 0xA0. Оригинал портит только A и флаги.
# Трансляция звала два IX-хелпера (485 тактов); здесь указатель в HL. 85 тактов.
# ---------------------------------------------------------------------------
N_BOTH_DEADLY = """set_both_deadly_flags:
				ASSERT (graphic_objs_tbl & 31) <= 18		; иначе l+13 переносит
				push	hl
				ld		hl, (z80_ix)
				ld		a, l
				add		a, 13
				ld		l, a
				ld		a, (hl)
				or		0xA0								; и сам смертелен, и его нельзя касаться
				ld		(hl), a
				pop		hl
				ret
; End of function set_both_deadly_flags
"""


# ---------------------------------------------------------------------------
# find_special_objs_here / update_special_objs — вход в комнату и выход из неё.
# Обе процедуры вызываются по одному разу на смену комнаты (в render_gate и локстепе
# их почти не видно), поэтому переписка чисто ради байт: 315+133 -> 120+65.
# Оригинал держал указатель записи в IY и вдобавок гонял ВТОРОЙ набор регистров (exx),
# чтобы одновременно иметь IY (таблица) и DE (курсор вывода). На i8080 exx разворачивается
# в 38 байт обмена с ячейками z80_*_alt, (iy+n) — в вызов хелпера: 5 exx + 4 (iy+n) = 315 байт.
# Здесь курсор таблицы живёт в HL, курсор вывода в DE, второй набор не нужен вовсе.
#
# Конец таблицы ловится по МЛАДШЕМУ байту: шаг 9, записей 32, и 9k mod 256 при k=0..32
# попарно различны (равенство требовало бы 9*(k1-k2)=256, а 256 на 9 не делится), поэтому
# l == low(special_objs_tbl_end) достигается ровно один раз — на конце таблицы.
# Регистры на выходе (A/F/BC/DE/HL) у обеих процедур мертвы: build_screen_objects сразу
# делает call clear_scrn_buffer / call adjust_plyr_xyz_for_room_size. IY воспроизводится
# точно (это ячейка памяти, живёт дальше): у первой = special_objs_tbl_end, у второй =
# other_objs_here — обе величины постоянны, цикл всегда доходит до конца таблицы.
# ---------------------------------------------------------------------------
N_FIND_SPEC_HERE = """find_special_objs_here:	; спецобъекты текущего экрана -> special_objs_here (2 записи), хвост обнулить
				ASSERT special_objs_tbl_end - special_objs_tbl == 288	; 32 записи по 9: конец ловится по младшему байту
				ASSERT (special_objs_here & 255) <= 190					; +64 не переносит в старший байт
				ld		b, (ix+8)							; текущий экран игрока (хелпер il_ix_ldb)
				ld		c, b								; C держит экран весь цикл (zero_DE портит только A/B)
				ld		hl, special_objs_tbl
				ld		de, special_objs_here
fso_1:			ld		a, (hl)								; graphic no
				or		a									; пусто?
				jp		z, fso_n
				push	hl
				ld		a, l
				add		a, 8
				ld		l, a
				ld		a, h
				adc		a, 0
				ld		h, a
				ld		a, (hl)								; экран объекта
				pop		hl
				cp		c									; тот же, что у игрока?
				jp		nz, fso_n
				push	hl									; указатель записи — в поля 16,17
				ld		a, (hl)
				ld		(de), a								; 0: graphic no
				inc		de
				ld		a, l
				add		a, 5
				ld		l, a
				ld		a, h
				adc		a, 0
				ld		h, a								; HL = запись+5 (X)
				ld		b, 3
fso_c:			ld		a, (hl)
				ld		(de), a
				inc		hl
				inc		de
				dec		b
				jp		nz, fso_c							; 1..3: X, Y, Z
				ld		a, 5
				ld		(de), a								; 4: ширина
				inc		de
				ld		(de), a								; 5: глубина
				inc		de
				ld		a, 12
				ld		(de), a								; 6: высота
				inc		de
				ld		a, 0x14
				ld		(de), a								; 7: флаги DRAW | MOVEABLE
				inc		de
				ld		a, (hl)								; запись+8 = экран
				ld		(de), a								; 8
				inc		de
				ld		b, 7
				call	zero_DE								; 9..15
				pop		hl
				push	hl
				ld		a, l
				ld		(de), a
				inc		de
				ld		a, h
				ld		(de), a								; 16,17: указатель записи special_objs_tbl
				inc		de
				ld		b, 14
				call	zero_DE								; 18..31
				pop		hl
fso_n:			ld		a, l								; следующая запись таблицы
				add		a, 9
				ld		l, a
				ld		a, h
				adc		a, 0
				ld		h, a
				ld		a, l
				cp		low special_objs_tbl_end
				jp		nz, fso_1
				ld		hl, special_objs_tbl_end			; IY после цикла — как у оригинала
				ld		(z80_iy), hl
fso_z:			ld		a, e								; хвост objs_here — нулями
				cp		low other_objs_here
				ret		z
				ld		b, 32
				call	zero_DE
				jp		fso_z
; End of function find_special_objs_here
"""

N_UPD_SPEC_OBJS = """update_special_objs:	; координаты из special_objs_here обратно в special_objs_tbl по указателю поля 16,17
				ASSERT (special_objs_here & 255) <= 190		; +49 и +64 не переносят в старший байт
				ld		hl, special_objs_here
uso_1:			ld		a, (hl)								; graphic no
				or		a									; пусто?
				jp		z, uso_n
				push	hl
				ld		a, l
				add		a, 16
				ld		l, a
				ld		e, (hl)
				inc		l
				ld		d, (hl)								; DE = указатель записи (у пузырей котла он нулевой — баг оригинала)
				pop		hl
				push	hl
				ld		a, (hl)
				ld		(de), a								; 0: graphic no
				ld		a, e
				add		a, 5
				ld		e, a
				ld		a, d
				adc		a, 0
				ld		d, a								; DE = запись+5
				inc		l									; HL = база+1 (X)
				ld		b, 3
uso_c:			ld		a, (hl)
				ld		(de), a
				inc		l
				inc		de
				dec		b
				jp		nz, uso_c							; 5..7: X, Y, Z
				pop		hl
				push	hl
				ld		a, l
				add		a, 8
				ld		l, a
				ld		a, (hl)								; экран
				ld		(de), a								; 8
				pop		hl
uso_n:			ld		a, l								; следующая запись objs_here
				add		a, 32
				ld		l, a
				cp		low other_objs_here					; конец таблицы (две записи)
				jp		nz, uso_1
				ld		hl, other_objs_here					; IY после цикла — как у оригинала
				ld		(z80_iy), hl
				ret
; End of function update_special_objs
"""



# ---------------------------------------------------------------------------
# Печать строк (display_menu / display_text_list / print_text* / loc_BE56..loc_BE72):
# снятие ВТОРОГО набора регистров, оставшегося без работы после выброса атрибутов ZX.
#
# На ZX процедура вела два курсора сразу: набор 1 — указатель на таблицу атрибутов
# (DE), набор 2 — координаты (HL), текст (DE), счётчик строк (B); плюс AF' хранил
# атрибут текущей строки, а HL' — адрес в атрибутной памяти. Атрибутная память на
# Векторе выброшена (PORT_RECIPE §17.3), и от всей этой машинерии остались только
# холостые exx/ex af,af': 9 exx по 38 Б и 4 ex af,af' по 21 Б = 426 Б, которые
# ничего не вычисляют. Проверка холостого хода:
#   * AF' восстанавливается: ровно один ex af,af' на входе (print_text/_single_colour)
#     и ровно один на выходе (loc_BE72); в теле цикла их ДВА подряд — это тождество;
#   * exx в конце тела и exx в начале loc_BE5F — тоже пара подряд, тождество;
#   * единственный не-холостой эффект — `pop hl` в loc_BE56 выполнялся во ВТОРОМ
#     наборе (чтобы не затереть адрес буфера в главном HL). Снятое со стека значение
#     (Y,X) равно тому, что лежит в BC после `pop bc/push bc`, поэтому `pop bc`
#     на его месте даёт и ту же глубину стека, и то же BC перед print_8x8;
#   * побочный след «HL' = Y,X» пропадает — но после этой переписки в программе НЕ
#     ОСТАЁТСЯ НИ ОДНОГО exx (все прочие уже сняты: см. grep по z80_bc_alt), значит
#     ячейки z80_bc_alt/z80_de_alt/z80_hl_alt больше никто не читает.
# Атрибут строки (tmp_attrib) теперь не пишется и не читается вовсе, поэтому из
# display_text_list уходит голова с exx, а из трёх вызывателей — `ld de,<цвета>`+`exx`.
# `bit 7,a` (8 Б после трансляции: ld (il_a),a / and 0x80 / ld a,(il_a)) заменён на
# `or a` + `jp m` (4 Б): знаковый бит A и так есть у i8080, а A остаётся целым для print_8x8.
# ---------------------------------------------------------------------------
N_TEXT_LIST = """display_text_list:	; HL = список координат (X,Y по 2 Б), DE = тексты подряд, B = число строк
				push	bc
				ld		a, (hl)								; X
				inc		hl
				inc		hl
				push	hl
				dec		hl
				ld		h, (hl)								; H = Y
				ld		l, a								; L = X
				call	print_text_single_colour
				pop		hl
				pop		bc
				djnz	display_text_list					; следующая строка списка
				ld		a, (suppress_border)
				and		a									; рисовать рамку?
				ret		NZ
				inc		a
				ld		(suppress_border), a
				call	print_border
				jp		update_screen
; End of function display_text_list
"""

N_PRINT_TEXT_SC = """print_text_single_colour:	; HL = Y,X; DE = текст (без байта атрибута)
				push	hl									; сохранить Y,X
				ld		hl, font
				ld		(gfxbase_8x8), hl
				pop		bc									; BC = Y,X
				push	bc
				call	vidbuf_calc_addr_in_hl
				jr		loc_BE56
; End of function print_text_single_colour
"""

N_PRINT_TEXT = """print_text_std_font:
				push	hl
				ld		hl, font
				ld		(gfxbase_8x8), hl

print_text:		; вход: на стеке Y,X; DE = байт атрибута ZX (пропускается), дальше текст
				pop		bc
				push	bc
				call	vidbuf_calc_addr_in_hl
				inc		de									; пропустить байт атрибута

loc_BE56:		pop		bc									; снять Y,X со стека (в оригинале — pop hl во втором наборе)

loc_BE5F:		ld		a, (de)								; следующий символ
				or		a									; бит 7 = последний?
				jp		m, loc_BE72
				push	de
				call	print_8x8
				pop		de
				inc		de
				jr		loc_BE5F

loc_BE72:		and		0x7F								; снять признак конца строки
				push	de
				call	print_8x8
				pop		de
				inc		de
				ret
; End of function print_text_std_font
"""


# ---------------------------------------------------------------------------
# set_wipe_and_draw_IY: «сделать то же самое, но для объекта в IY».
# Оригинал — восемь команд с IX/IY (push iy/push ix/push iy/pop ix/call/pop ix/pop iy),
# после трансляции 64 Б: каждый push/pop индексного регистра — это обмен с ячейкой.
# IY сохранять ОБЯЗАТЕЛЬНО: set_draw_objs_overlapped (нативная) пишет z80_iy в конце.
# ---------------------------------------------------------------------------
N_SET_WD_IY = """set_wipe_and_draw_IY:	; WIPE|DRAW объекту (z80_iy); IX и IY восстанавливаются
				ld		hl, (z80_ix)
				push	hl
				ld		hl, (z80_iy)
				push	hl
				ld		(z80_ix), hl						; IX = IY
				call	set_wipe_and_draw_flags
				pop		hl
				ld		(z80_iy), hl						; set_draw_objs_overlapped портит IY
				pop		hl
				ld		(z80_ix), hl
				ret
; End of function set_wipe_and_draw_IY
"""

# ---------------------------------------------------------------------------
# prepare_final_animation: два прохода по таблице объектов с указателем в IX.
# Первый — пометить 11 объектов комнаты WIPE|DRAW и объявить их несуществующими;
# второй — заменить блоки (graphic 7) на искристый спрайт 131 до конца таблицы (clear_end).
# Указатель переезжает в HL; z80_ix заполняется только там, где его читает вызываемая
# процедура (set_wipe_and_draw_flags -> set_draw_objs_overlapped), и восстанавливается на выходе.
# Конец таблицы — точное равенство не годится (шаг 32 от special_objs_here_1, а сравнение
# идёт с clear_end), поэтому оставлено 16-битное вычитание, но без push ix/pop hl.
# HL/DE/BC/A на выходе мертвы: единственный вызыватель (chk_all_objs_in_cauldron) сразу делает ret.
# ---------------------------------------------------------------------------
N_PREP_FINAL = """prepare_final_animation:
				ld		a, 1
				ld		(all_objs_in_cauldron), a
				ld		hl, (z80_ix)
				push	hl									; сохранить IX
				ld		hl, special_objs_here_1				; special_objs_here[1]
				ld		b, 11								; 11 объектов комнаты (1 спец, 7 фоновых, 3 передних)
pfa_1:			ld		(z80_ix), hl
				push	bc
				push	hl
				call	set_wipe_and_draw_flags
				pop		hl
				pop		bc
				ld		(hl), 1								; graphic no = INVALID
				ld		a, l								; следующая запись
				add		a, 32
				ld		l, a
				ld		a, h
				adc		a, 0
				ld		h, a
				dec		b
				jp		nz, pfa_1
pfa_2:			ld		a, (hl)								; graphic no
				cp		7									; блок?
				jp		nz, pfa_3
				ld		(hl), 131							; искристый спрайт
pfa_3:			ld		a, l
				add		a, 32
				ld		l, a
				ld		a, h
				adc		a, 0
				ld		h, a
				ld		a, l								; конец таблицы объектов?
				sub		low clear_end
				ld		a, h
				sbc		a, high clear_end
				jp		c, pfa_2
				pop		hl
				ld		(z80_ix), hl						; вернуть IX
				ret
; End of function prepare_final_animation
"""


# ---------------------------------------------------------------------------
# upd_103 (объект «лишняя жизнь») и loc_C0DD (сброс объекта): голова обеих процедур —
# это перекладывание IX/IY друг в друга. На i8080 каждый push/pop индексного регистра —
# 10 Б (сохранить HL, прочитать ячейку, push, вернуть HL), а `ld ix,nn` — 12 Б.
# Пишем ячейки напрямую; `push iy / pop ix` воспроизводим как `ld hl,(z80_iy)` +
# `ld (z80_ix),hl` — это точная семантика оригинала ДАЖЕ если вызванная процедура
# успела изменить IY (сохранённая копия такой гарантии не дала бы).
# Флаг CY между call is_on_or_near_obj и jp nc жив: inc/dec (hl), ld hl,nn и ld (nn),hl
# переноса не трогают.
# ---------------------------------------------------------------------------
N_UPD_103 = """upd_103:		; объект «лишняя жизнь»: игрок с расширенной на 1 коробкой рядом?
				call	upd_128_to_130
				ld		hl, (z80_ix)
				ld		(z80_iy), hl						; IY = объект «лишняя жизнь»
				ld		hl, graphic_objs_tbl
				ld		(z80_ix), hl						; IX = игрок
				ld		hl, graphic_objs_tbl+4
				inc		(hl)								; ширина игрока +1
				inc		hl
				inc		(hl)								; глубина +1
				call	is_on_or_near_obj
				ld		hl, graphic_objs_tbl+4
				dec		(hl)								; вернуть ширину (inc/dec не трогают CY)
				inc		hl
				dec		(hl)								; вернуть глубину
				ld		hl, (z80_iy)
				ld		(z80_ix), hl						; IX снова объект «лишняя жизнь»
				jp		nc, loc_C1EE						; не рядом — пропустить
"""

N_LOC_C0DD = """loc_C0DD:
				push	hl									; указатель в таблице сброса
				ld		hl, (z80_iy)
				inc		hl
				ex		de, hl								; DE = объект+1
				ld		hl, (z80_ix)
				inc		hl									; HL = игрок+1 (X)
				ld		bc, 3
				call	vec_ldir_fwd						; копия X, Y, Z игрока
				ld		a, (ix+3)							; Z
				add		a, 12
				ld		(ix+3), a							; поднять Z
				ld		a, (ix+0x23)						; Z второго спрайта
				add		a, 12
				ld		(ix+0x23), a
				ld		hl, (z80_ix)
				push	hl									; сохранить IX
				ld		hl, (z80_iy)
				ld		(z80_ix), hl						; IX = IY
				call	calc_pixel_XY
				pop		hl
				ld		(z80_ix), hl						; вернуть IX
"""


def _sub(src, pattern, repl, name, flags=re.S):
    src, n = re.subn(pattern, lambda m: repl, src, count=1, flags=flags)
    assert n == 1, "kl_native_logic: не найдено: " + name
    return src


def apply(src, attrs_cut=False):
    """attrs_cut=True — цель vec. Что под этим флагом и почему только vec:
      * печать строк: атрибутная память ZX вырезана (kl_cut_zx_attributes), второй набор
        регистров остался без работы. В цели lockstep атрибуты на месте — там логика разойдётся с ZX;
      * loc_D653/.copy_loop: в цели lockstep kl_logic_fixes уже переписал этот цикл на счётчик
        в памяти (квирк оригинала 0xD666), ex af,af' там нет;
      * START: перестановка чтения затравки за clr_mem законна, потому что 0x5C78 у ЦЕЛИ VEC
        лежит в коде, а не в очищаемой области (clear_start = 0x7A80)."""
    src = _sub(src, r'(?m)^is_near_to:\n.*?; End of function is_near_to\r?\n', N_IS_NEAR_TO, 'is_near_to')
    src = _sub(src, r'(?m)^chk_plyr_spec_near_arch:\n.*?; End of function chk_plyr_spec_near_arch\r?\n',
               N_CHK_NEAR_ARCH, 'chk_plyr_spec_near_arch')
    src = _sub(src, r'(?m)^set_draw_objs_overlapped:.*?; End of function set_draw_objs_overlapped\r?\n',
               N_SET_DRAW, 'set_draw_objs_overlapped')
    src = _sub(src, r'(?m)^upd_3_5:[^\n]*\n.*?(?=^; =============== S U B R O U T I N E)', N_UPD_3_5, 'upd_3_5')
    src = _sub(src, r'(?m)^upd_2_4:[^\n]*\n.*?(?=^chk_plyr_spec_near_arch:)', N_UPD_2_4, 'upd_2_4 + цикл арки')
    for lbl, txt, end in (('do_objs_intersect_on_x', N_DOI_X, 'do_objs_intersect_on_x'),
                          ('do_objs_intersect_on_y', N_DOI_Y, 'do_objs_intersect_on_y'),
                          ('do_objs_intersect_on_z', N_DOI_Z, 'do_objs_intersect_on_z'),
                          ('chk_plyr_OOB', N_CHK_OOB, 'chk_plyr_OOB'),
                          ('clear_dX_dY', N_CLEAR_DXDY, 'move_player'),
                          ('adj_dZ_for_out_of_bounds', N_ADJ_DZ_OOB, 'adj_dZ_for_out_of_bounds'),
                          ('adj_dX_for_out_of_bounds', N_ADJ_DX_OOB, 'adj_dX_for_out_of_bounds'),
                          ('adj_dY_for_out_of_bounds', N_ADJ_DY_OOB, 'adj_dY_for_out_of_bounds'),
                          ('dec_dZ_and_update_XYZ', N_ADD_DXYZ, 'dec_dZ_and_update_XYZ'),
                          ('toggle_next_prev_sprite', N_TOGGLE_SPR, 'toggle_next_prev_sprite'),
                          ('adj_for_out_of_bounds', N_ADJ_OOB, 'adj_for_out_of_bounds')):
        src = _sub(src, r'(?m)^' + lbl + r':[^\n]*\n.*?; End of function ' + end + r'\r?\n', txt, lbl)
    src = _sub(src, r'(?m)^move_player:[^\n]*\n.*?(?=^clear_dX_dY:)', N_MOVE_PLAYER, 'move_player')
    src = _sub(src, r'(?m)^flip_sprite:[^\n]*\n.*?(?=^; =============== S U B R O U T I N E)', N_FLIP_HEAD, 'flip_sprite (голова)')
    src = _sub(src, r'(?m)^upd_80_to_83:[^\n]*\n.*?(?=^; =============== S U B R O U T I N E)', N_UPD_80_83, 'upd_80_to_83')
    src = _sub(src, r'(?m)^upd_player_bottom:[^\n]*\n.*?(?=^; returns NC if out-of-bounds)', N_UPD_PLAYER_BOTTOM, 'upd_player_bottom')
    src = _sub(src, r'(?m)^upd_player_top:[^\n]*\n.*?(?=^; =============== S U B R O U T I N E)', N_UPD_PLAYER_TOP, 'upd_player_top')
    src = _sub(src, r'(?m)^calc_2d_info:[^\n]*\n.*?; End of function calc_2d_info\r?\n', N_CALC_2D, 'calc_2d_info')
    src = _sub(src, r'(?m)^set_wipe_and_draw_flags:[^\n]*\n.*?; End of function set_wipe_and_draw_flags\r?\n',
               N_SET_WD, 'set_wipe_and_draw_flags')
    src = _sub(src, r'(?m)^handle_exit_screen:[^\n]*\n.*?; End of function handle_exit_screen\r?\n',
               N_HANDLE_EXIT, 'handle_exit_screen')
    src = _sub(src, r'(?m)^list_objects_to_draw:[^\n]*\n.*?; End of function list_objects_to_draw\r?\n',
               N_LIST_OBJS, 'list_objects_to_draw')
    src = _sub(src, r'(?m)^zero_end_of_graphic_objs_tbl:[^\n]*\n.*?^[ \t]*jr[ \t]+zero_end_of_graphic_objs_tbl[^\n]*\n',
               N_ZERO_END, 'zero_end_of_graphic_objs_tbl (счётчик занятых записей)')
    src = _sub(src, r'(?m)^wipe_next_object:[^\n]*\n.*?(?=^loc_D653:)', N_WIPE, 'wipe_next_object')
    src = _sub(src, r'(?m)^set_pixel_adj:[^\n]*\n.*?; End of function set_pixel_adj\r?\n', N_SET_PIXEL_ADJ, 'set_pixel_adj')
    src = _sub(src, r'(?m)^calc_pixel_XY_and_render:\n.*?; End of function calc_pixel_XY_and_render\r?\n',
               N_CALC_PXY_RENDER, 'calc_pixel_XY_and_render')
    src = _sub(src, r'(?m)^set_both_deadly_flags:\n.*?; End of function set_both_deadly_flags\r?\n',
               N_BOTH_DEADLY, 'set_both_deadly_flags')
    src = _sub(src, r'(?m)^get_ptr_object:\n.*?; End of function get_ptr_object\r?\n', N_GET_PTR_OBJ, 'get_ptr_object')
    src = _sub(src, r'(?m)^calc_display_order_and_render:\n.*?; End of function calc_display_order_and_render\r?\n',
               N_DISPLAY_ORDER, 'calc_display_order_and_render')
    # циклы класса пересечений: указатель объекта 2 в HL, шаг 32 в DE, is_object_not_ignored по месту
    for lbl, txt, end in (('adj_dX_for_obj_intersect', N_ADJ_DX_LOOP, 'adj_dX_for_obj_intersect'),
                          ('adj_dY_for_obj_intersect', N_ADJ_DY_LOOP, 'adj_dY_for_obj_intersect'),
                          ('adj_dZ_for_obj_intersect', N_ADJ_DZ_LOOP, 'adj_dZ_for_obj_intersect'),
                          ('do_any_objs_intersect', N_DO_ANY, 'do_any_objs_intersect'),
                          ('is_on_or_near_obj', N_ON_OR_NEAR, 'can_pickup_spec_obj')):
        src = _sub(src, r'(?m)^' + lbl + r':[^\n]*\n.*?; End of function ' + end + r'\r?\n', txt, lbl)
    # печать строк: снятие второго набора регистров (атрибуты ZX выброшены) — только цель vec
    if attrs_cut:
        src = _sub(src, r'(?m)^display_text_list:[^\n]*\n.*?; End of function display_text_list\r?\n',
                   N_TEXT_LIST, 'display_text_list')
        src = _sub(src, r'(?m)^print_text_single_colour:[^\n]*\n.*?; End of function print_text_single_colour\r?\n',
                   N_PRINT_TEXT_SC, 'print_text_single_colour')
        src = _sub(src, r'(?m)^print_text_std_font:[^\n]*\n.*?; End of function print_text_std_font\r?\n',
                   N_PRINT_TEXT, 'print_text_std_font')
        # вызыватели display_text_list: таблица атрибутов и переключение набора больше не нужны
        # ВНИМАНИЕ: у display_menu нельзя трогать `ld b, N` — kl_cut_controls уже переписал 8 на 4
        # (меню урезано до 4 строк). Снимаем ровно две строки: указатель на цвета и exx.
        for lbl in ('menu_colours', 'gameover_colours', 'complete_colours'):
            src = _sub(src, r'(?m)^[ \t]*ld[ \t]+de,[ \t]*' + lbl + r'[^\n]*\n[ \t]*exx[^\n]*\n', '',
                       'снятие exx у вызывателя display_text_list (' + lbl + ')')
        # loc_D653/.copy_loop: счётчик копий жил в AF' (2 x ex af,af' = 42 Б после трансляции).
        # Стек занят параметрами blit_to_screen (pop bc/hl/de), поэтому счётчик — в ячейку kl_cpcnt.
        # КЛИП ПО ГРАНИЦЕ HUD (переехал сюда из blit_to_screen, §29; граница стала ступенчатой, §30).
        # Панель живёт ТОЛЬКО на экране: update_screen копирует буфер и тут же его обнуляет, поэтому
        # окно объекта, попавшее в панель, копирует на неё нули, и до следующей смены комнаты дыра
        # не зарастает. Панель — НЕ ТОЛЬКО строки 32..63: display_panel рисует боковые колонны и
        # лестницу, обходящую нижние углы ромба (kl_hudtop: первая строка поля по паре столбцов).
        # Граница берётся по КРАЯМ окна и выбирается ВЫСШАЯ из двух: окно прямоугольное, ступенька
        # монотонна, поэтому максимум лестницы под окном всегда на одном из его краёв.
        # Окно целиком под краем панели не копируется вовсе. В blit_to_screen эту проверку держать
        # было нельзя: туда же приходят окна самой панели (рамка солнца 32..62, ячейки предметов
        # 32..55, счётчик дней 32..39). Через .copy_loop окна панели не идут ни одно.
        src = _sub(src, r"(?m)^[ \t]*; copy object from video buffer to screen\r?\n[ \t]*call[ \t]+blit_to_screen[^\n]*\r?\n",
                   "\t\t\t\t; copy object from video buffer to screen (через клип по краю панели)\n"
                   "\t\t\t\tcall    kl_hudclip\n",
                   'loc_D653 .copy_loop: клип по лестнице панели')
        src = _sub(src, r"(?m)^\.copy_loop:[ \t]*ex[ \t]+af,[ \t]*af'[^\n]*\n",
                   "\t\t\t\t; счётчик копий: blit_to_screen портит A, а стек занят параметрами\n"
                   ".copy_loop:\t\tld\t\t(kl_cpcnt), a\n", 'loc_D653 .copy_loop вход')
        src = _sub(src, r"(?m)^[ \t]*ex[ \t]+af,[ \t]*af'[^\n]*\n[ \t]*dec[ \t]+a[^\n]*\n"
                        r"[ \t]*jr[ \t]+nz,[ \t]*\.copy_loop[^\n]*\n",
                   "\t\t\t\tld\t\ta, (kl_cpcnt)\n\t\t\t\tdec\t\ta\n\t\t\t\tjr\t\tnz, .copy_loop\n",
                   'loc_D653 .copy_loop выход')
        # START: байт-затравка seed_1 бралась ДО clr_mem и пряталась в AF' (2 x ex af,af' = 42 Б).
        # На Векторе адрес 0x5C78 (на ZX это системная FRAMES) лежит в КОДЕ, а не в очищаемой
        # области (clear_start = 0x7A80), поэтому читать его можно и ПОСЛЕ clr_mem — байт тот же.
        src = _sub(src, r"(?m)^[ \t]*ld[ \t]+a,[ \t]*\(0x5C78\)[^\n]*\n[ \t]*ex[ \t]+af,[ \t]*af'[^\n]*\n"
                        r"[ \t]*call[ \t]+clr_mem[^\n]*\n[ \t]*ex[ \t]+af,[ \t]*af'[^\n]*\n",
                   "\t\t\t\tcall\tclr_mem\n"
                   "\t\t\t\tld\t\ta, (0x5C78)\t\t; затравка seed_1: байт кода (на ZX — FRAMES), вне очищаемой области\n",
                   "START: затравка seed_1 без AF'")
    src = _sub(src, r'(?m)^upd_103:[^\n]*\n.*?^[ \t]*jr[ \t]+NC,[ \t]*loc_C1EE[^\n]*\n', N_UPD_103, 'upd_103')
    src = _sub(src, r'(?m)^loc_C0DD:[^\n]*\n.*?(?=^drop_object:)', N_LOC_C0DD, 'loc_C0DD')
    src = _sub(src, r'(?m)^set_wipe_and_draw_IY:[^\n]*\n.*?; End of function set_wipe_and_draw_IY\r?\n',
               N_SET_WD_IY, 'set_wipe_and_draw_IY')
    src = _sub(src, r'(?m)^prepare_final_animation:[^\n]*\n.*?; End of function prepare_final_animation\r?\n',
               N_PREP_FINAL, 'prepare_final_animation')
    src = _sub(src, r'(?m)^find_special_objs_here:[^\n]*\n.*?; End of function find_special_objs_here\r?\n',
               N_FIND_SPEC_HERE, 'find_special_objs_here')
    src = _sub(src, r'(?m)^update_special_objs:[^\n]*\n.*?; End of function update_special_objs\r?\n',
               N_UPD_SPEC_OBJS, 'update_special_objs')
    # is_object_not_ignored больше никто не вызывает — процедуру убираем
    src = _sub(src, r'(?m)^is_object_not_ignored:[^\n]*\n.*?; End of function is_object_not_ignored\r?\n', '', 'снятие is_object_not_ignored')
    # ret_from_tbl_jp: байтовый инкремент z80_ix вместо 16-битного, сравнение по младшему байту
    src = _sub(src, r'(?m)^ret_from_tbl_jp:.*?(?=^loc_B000:)', N_RET_FROM_TBL, 'ret_from_tbl_jp')
    return src
