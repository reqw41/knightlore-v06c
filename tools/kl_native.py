"""Нативные i8080-версии горячих процедур Knight Lore (по спецификациям spec/kl_spec.py).
Подставляются в исходник ДО трансляции для целей i8080 (lockstep, vec). Всё в подмножестве i8080
(мнемоники Z80). Объект «IX» берётся из ячейки z80_ix, «IY» — z80_iy (контракт транслятора)."""
import re

SCRATCH = """
kl_o1:			DS		6			; поля X Y Z W D H объекта 1 (класс перекрытия)
kl_o2:			DS		6			; то же объекта 2
kl_w2:			DS		1			; флип: ширина*2
kl_w:			DS		1			; флип: ширина (пар)
kl_h:			DS		1			; флип: строк
kl_cnt:			DS		1
kl_cnt2:		DS		1
kl_pw:			DS		1			; перекрытие: поля 24..31 объекта
kl_ph:			DS		1
kl_px:			DS		1
kl_py:			DS		1
kl_opw:			DS		1
kl_oph:			DS		1
kl_opx:			DS		1
kl_opy:			DS		1
kl_e:			DS		1			; левый край (байт), объединённая ширина, нижний Y, объединённая высота
kl_d:			DS		1
kl_lo:			DS		1
kl_hh:			DS		1
"""

N_SAVE_2D = """save_2d_info:	; obj[28..31] = obj[24..27]; A = obj[27] (как оригинал). BC/DE/HL сохраняются, CY портится.
				push	hl
				push	de
				ld		hl, (z80_ix)
				ld		de, 24
				add		hl, de
				ld		d, h
				ld		e, l
				inc		de
				inc		de
				inc		de
				inc		de
				ld		a, (hl)
				ld		(de), a
				inc		hl
				inc		de
				ld		a, (hl)
				ld		(de), a
				inc		hl
				inc		de
				ld		a, (hl)
				ld		(de), a
				inc		hl
				inc		de
				ld		a, (hl)
				ld		(de), a
				pop		de
				pop		hl
				ret
; End of function save_2d_info
"""

N_CALC_PIXEL_XY = """calc_pixel_XY:	; obj[26] = X+Y-128+adjX; obj[27] = ((Y-X+128)>>1)+Z-104+adjY; CY = pixelY < 192. Портит BC/DE/HL.
				ld		hl, (z80_ix)
				inc		hl
				ld		b, (hl)								; X
				inc		hl
				ld		c, (hl)								; Y
				inc		hl
				ld		d, (hl)								; Z
				ld		a, l
				add		a, 15
				ld		l, a
				ld		a, h
				adc		a, 0
				ld		h, a								; -> +18 adjX
				ld		a, b
				add		a, c
				sub		128
				add		a, (hl)
				ld		e, a								; pixel X
				inc		hl									; +19 adjY
				ld		a, c
				sub		b
				add		a, 128
				or		a
				rra
				add		a, d
				sub		104
				add		a, (hl)
				ld		c, a								; pixel Y
				ld		a, l
				add		a, 7
				ld		l, a
				ld		a, h
				adc		a, 0
				ld		h, a								; -> +26
				ld		(hl), e
				inc		hl
				ld		(hl), c
				ld		a, c
				cp		192
				ret
"""

N_LIST_OBJS = """list_objects_to_draw:	; индексы объектов с graphic_no!=0 и DRAW (бит 4 поля 7) -> objects_to_draw, терминатор 0xFF
				ld		hl, graphic_objs_tbl
				ld		de, objects_to_draw
				ld		c, 0
				ld		b, 40
lo_1:			ld		a, (hl)
				or		a
				jp		z, lo_2
				push	hl
				ld		a, l
				add		a, 7
				ld		l, a
				ld		a, h
				adc		a, 0
				ld		h, a
				ld		a, (hl)
				pop		hl
				and		0x10
				jp		z, lo_2
				ld		a, c
				ld		(de), a
				inc		de
lo_2:			inc		c
				ld		a, l
				add		a, 32
				ld		l, a
				ld		a, h
				adc		a, 0
				ld		h, a
				dec		b
				jp		nz, lo_1
				ld		a, 0xFF
				ld		(de), a
				ret
; End of function list_objects_to_draw
"""

# флип спрайта: DE -> спрайт (сохраняется), объект (z80_ix)[7] — требуемая ориентация
N_FLIP = """flip_sprite_data:	; привести хранимую ориентацию спрайта (биты 7 V, 6 H байта 0) к obj[7]. DE сохраняется.
				ld		hl, (z80_ix)
				ld		bc, 7
				add		hl, bc
				ld		a, (de)
				xor		(hl)
				and		0xC0
				ret		z
				push	de
				jp		p, fl_h								; бит 7 = 0 -> только H
				; ---- V-флип: обмен строк i <-> h-1-i ----
				ld		a, (de)
				xor		0x80
				ld		(de), a
				and		0x0F
				add		a, a
				ld		(kl_w2), a							; w2 = 2*w
				inc		de
				ld		a, (de)
				ld		(kl_h), a
				inc		de									; DE = данные (строка 0)
				ld		h, d
				ld		l, e
				ld		a, (kl_h)
				or		a
				jp		z, fl_v_end
				dec		a
				jp		z, fl_v_end							; h = 1: нечего менять
				ld		b, a								; h-1
fl_vq:			ld		a, (kl_w2)
				add		a, l
				ld		l, a
				ld		a, h
				adc		a, 0
				ld		h, a
				dec		b
				jp		nz, fl_vq							; HL = последняя строка
				ld		a, (kl_h)
				or		a
				rra
				ld		(kl_cnt2), a						; пар строк = h/2
fl_vrow:		ld		a, (kl_w2)
				ld		(kl_cnt), a
fl_vb:			ld		a, (de)
				ld		b, (hl)
				ld		(hl), a
				ld		a, b
				ld		(de), a
				inc		hl
				inc		de
				ld		a, (kl_cnt)
				dec		a
				ld		(kl_cnt), a
				jp		nz, fl_vb
				ld		a, (kl_w2)
				add		a, a
				ld		b, a
				ld		a, l
				sub		b
				ld		l, a
				ld		a, h
				sbc		a, 0
				ld		h, a								; HL = предыдущая строка снизу
				ld		a, (kl_cnt2)
				dec		a
				ld		(kl_cnt2), a
				jp		nz, fl_vrow
fl_v_end:		pop		de
				push	de
				; ---- H-флип? ----
fl_h:			ld		hl, (z80_ix)
				ld		bc, 7
				add		hl, bc
				ld		a, (de)
				xor		(hl)
				add		a, a
				jp		p, fl_ret							; бит 6 = 0 -> готово
				ld		a, (de)
				xor		0x40
				ld		(de), a
				and		0x0F
				ld		(kl_w), a
				inc		de
				ld		a, (de)
				ld		(kl_h), a
				inc		de									; DE = начало строки
				or		a
				jp		z, fl_ret
fl_hrow:		push	de									; начало строки
				ld		h, d
				ld		l, e
				ld		a, (kl_w)
				add		a, a
				dec		a
				dec		a
				add		a, l
				ld		l, a
				ld		a, h
				adc		a, 0
				ld		h, a								; HL = последняя пара (R), DE = первая (L)
				ld		a, (kl_w)
				or		a
				rra
				ld		(kl_cnt), a							; обменов = w/2
				jp		z, fl_hmid
fl_hswap:		ld		a, (de)
				push	hl
				ld		h, high reverse_bits_table
				ld		l, a
				ld		a, (hl)
				pop		hl
				ld		b, a								; B = rev(mask L)
				ld		a, (hl)
				push	hl
				ld		h, high reverse_bits_table
				ld		l, a
				ld		a, (hl)
				pop		hl
				ld		(de), a								; (L).mask = rev(mask R)
				ld		(hl), b								; (R).mask = rev(mask L)
				inc		de
				inc		hl
				ld		a, (de)
				push	hl
				ld		h, high reverse_bits_table
				ld		l, a
				ld		a, (hl)
				pop		hl
				ld		b, a
				ld		a, (hl)
				push	hl
				ld		h, high reverse_bits_table
				ld		l, a
				ld		a, (hl)
				pop		hl
				ld		(de), a
				ld		(hl), b
				inc		de									; L -> следующая пара
				dec		hl
				dec		hl
				dec		hl									; R -> предыдущая пара
				ld		a, (kl_cnt)
				dec		a
				ld		(kl_cnt), a
				jp		nz, fl_hswap
fl_hmid:		ld		a, (kl_w)
				and		1
				jp		z, fl_hnext
				ld		a, (de)								; средняя пара: только бит-реверс
				push	hl
				ld		h, high reverse_bits_table
				ld		l, a
				ld		a, (hl)
				pop		hl
				ld		(de), a
				inc		de
				ld		a, (de)
				push	hl
				ld		h, high reverse_bits_table
				ld		l, a
				ld		a, (hl)
				pop		hl
				ld		(de), a
fl_hnext:		pop		de									; начало строки
				ld		a, (kl_w)
				add		a, a
				add		a, e
				ld		e, a
				ld		a, d
				adc		a, 0
				ld		d, a								; следующая строка
				ld		a, (kl_h)
				dec		a
				ld		(kl_h), a
				jp		nz, fl_hrow
fl_ret:			pop		de
				ret
"""

# класс перекрытия (внутри process_remaining_objs): вход — объекты в (z80_ix)/(z80_iy); выход — C = класс, L = C
N_OVERLAP = """				push	de						; DE = указатель списка objects_to_draw (жив во внешнем цикле)
				ld		hl, (z80_ix)
				inc		hl
				ld		de, kl_o1
				ld		b, 6
ov_c1:			ld		a, (hl)
				ld		(de), a
				inc		hl
				inc		de
				dec		b
				jp		nz, ov_c1
				ld		hl, (z80_iy)
				inc		hl
				ld		de, kl_o2
				ld		b, 6
ov_c2:			ld		a, (hl)
				ld		(de), a
				inc		hl
				inc		de
				dec		b
				jp		nz, ov_c2
				ld		c, 0
				; Z: если Z1 >= Z2+H2 -> 0; иначе если Z2 < Z1+H1 -> 1 иначе 2
				ld		a, (kl_o2+2)
				ld		hl, kl_o2+5
				add		a, (hl)
				ld		b, a
				ld		a, (kl_o1+2)
				cp		b
				jp		nc, ov_y
				ld		a, (kl_o1+2)
				ld		hl, kl_o1+5
				add		a, (hl)
				ld		b, a
				ld		a, (kl_o2+2)
				cp		b
				jp		c, ov_z1
				inc		c
ov_z1:			inc		c
				; Y: если (Y1-D1) >= Y2+D2 -> 0; иначе если (Y2-D2) < Y1+D1 -> 3 иначе 6
ov_y:			ld		a, (kl_o2+1)
				ld		hl, kl_o2+4
				add		a, (hl)
				ld		b, a
				ld		a, (kl_o1+1)
				ld		hl, kl_o1+4
				sub		(hl)
				cp		b
				jp		nc, ov_x
				ld		a, (kl_o1+1)
				add		a, (hl)
				ld		b, a
				ld		a, (kl_o2+1)
				ld		hl, kl_o2+4
				sub		(hl)
				cp		b
				ld		a, c
				jp		c, ov_y1
				add		a, 3
ov_y1:			add		a, 3
				ld		c, a
				; X: аналогично с W: 9 / 18
ov_x:			ld		a, (kl_o2+0)
				ld		hl, kl_o2+3
				add		a, (hl)
				ld		b, a
				ld		a, (kl_o1+0)
				ld		hl, kl_o1+3
				sub		(hl)
				cp		b
				jp		nc, ov_end
				ld		a, (kl_o1+0)
				add		a, (hl)
				ld		b, a
				ld		a, (kl_o2+0)
				ld		hl, kl_o2+3
				sub		(hl)
				cp		b
				ld		a, c
				jp		c, ov_x1
				add		a, 9
ov_x1:			add		a, 9
				ld		c, a
ov_end:			pop		de
"""

N_SET_DRAW = """set_draw_objs_overlapped:	; calc_2d_info, затем DRAW всем объектам, попавшим в объединённый прямоугольник (см. спецификацию)
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
sd_1:			ld		(kl_e), a							; левый край
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
sd_2:			ld		hl, kl_e
				sub		(hl)
				ld		(kl_d), a							; объединённая ширина
				ld		a, (kl_py)
				ld		hl, kl_opy
				cp		(hl)
				jp		c, sd_3
				ld		a, (hl)
sd_3:			ld		(kl_lo), a							; нижний Y
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
sd_4:			ld		hl, kl_lo
				sub		(hl)
				ld		(kl_hh), a							; объединённая высота
				ld		hl, graphic_objs_tbl
				ld		b, 40
sd_loop:		ld		a, (hl)
				or		a
				jp		z, sd_next
				push	hl
				ld		a, l
				add		a, 7
				ld		l, a
				ld		a, h
				adc		a, 0
				ld		h, a
				ld		a, (hl)
				pop		hl
				and		0x10
				jp		nz, sd_next
				push	hl
				ld		de, 26
				add		hl, de								; -> pixel X
				ld		a, (hl)
				rrca
				rrca
				rrca
				and		0x1F
				ld		c, a								; ax
				ld		a, (kl_e)
				ld		e, a
				ld		a, c
				sub		e
				jp		c, sd_xneg
				ld		e, a
				ld		a, (kl_d)
				ld		d, a
				ld		a, e
				cp		d
				jp		nc, sd_pn
				jp		sd_y
sd_xneg:		cpl
				inc		a									; e - ax
				ld		e, a
				dec		hl
				dec		hl									; -> pw объекта
				ld		a, (hl)
				ld		d, a
				ld		a, e
				cp		d
				jp		nc, sd_pn
				inc		hl
				inc		hl
sd_y:			inc		hl									; -> pixel Y
				ld		a, (hl)
				ld		c, a
				ld		a, (kl_lo)
				ld		e, a
				ld		a, c
				sub		e
				jp		c, sd_yneg
				ld		e, a
				ld		a, (kl_hh)
				ld		d, a
				ld		a, e
				cp		d
				jp		nc, sd_pn
				jp		sd_set
sd_yneg:		cpl
				inc		a
				ld		e, a
				dec		hl
				dec		hl									; -> ph объекта
				ld		a, (hl)
				ld		d, a
				ld		a, e
				cp		d
				jp		nc, sd_pn
sd_set:			pop		hl
				push	hl
				ld		a, l
				add		a, 7
				ld		l, a
				ld		a, h
				adc		a, 0
				ld		h, a
				ld		a, (hl)
				or		0x10
				ld		(hl), a
sd_pn:			pop		hl
sd_next:		ld		de, 32
				add		hl, de
				dec		b
				jp		nz, sd_loop
				ld		hl, graphic_objs_tbl+1280			; IY после цикла = конец таблицы (как оригинал)
				ld		(z80_iy), hl
				ret
; End of function set_draw_objs_overlapped
"""

N_SET_PIXEL_ADJ = """set_pixel_adj:	; obj[18] = L, obj[19] = H. AF/HL/DE сохраняются (оригинал не трогает A).
				push	af
				push	hl
				push	de
				ex		de, hl								; DE = значение
				ld		hl, (z80_ix)
				ld		a, l
				add		a, 18
				ld		l, a
				ld		a, h
				adc		a, 0
				ld		h, a
				ld		(hl), e
				inc		hl
				ld		(hl), d
				pop		de
				pop		hl
				pop		af
				ret
; End of function set_pixel_adj
"""

N_RET_FROM_TBL = """ret_from_tbl_jp:	; (ld a,r -> 0) seed_3 += 0; IX += 32; если IX >= конец таблицы -> loc_B000, иначе следующий объект
				ld		hl, (z80_ix)
				ld		de, 32
				add		hl, de
				ld		(z80_ix), hl
				ld		de, clear_end
				ld		a, l
				sub		e
				ld		a, h
				sbc		a, d
				jp		nc, loc_B000
				jp		update_sprite_loop
"""

def _sub(src, pattern, repl, name, flags=re.S):
    src, n = re.subn(pattern, lambda m: repl, src, count=1, flags=flags)
    assert n == 1, "kl_native: не найдено: " + name
    return src

def apply(src):
    """Подставить нативные процедуры в исходник (Z80-синтаксис, подмножество i8080)."""
    src = _sub(src, r'save_2d_info:\n.*?; End of function save_2d_info\r?\n', N_SAVE_2D, 'save_2d_info')
    src = _sub(src, r'calc_pixel_XY:\n.*?\n\t+ret\r?\n', N_CALC_PIXEL_XY, 'calc_pixel_XY')
    src = _sub(src, r'list_objects_to_draw:\n.*?; End of function list_objects_to_draw\r?\n', N_LIST_OBJS, 'list_objects_to_draw')
    src = _sub(src, r'flip_sprite_data:[^\n]*\n.*?(?=Copyright1984A_c_g_:)', N_FLIP + "\n", 'flip_sprite_data')
    src = _sub(src, r'(?<=jr      Z, loc_CEDB                             ; yes, loop\n)\t+ld      c, 0\n\t+ld      a, \(iy\+3\).*?loc_CF62:\n\t+ld      l, c\r?\n', N_OVERLAP + "loc_CF62:\n\t\t\t\tld      l, c\n", 'overlap class')
    src = _sub(src, r'set_draw_objs_overlapped:\n.*?; End of function set_draw_objs_overlapped\r?\n', N_SET_DRAW, 'set_draw_objs_overlapped')
    src = _sub(src, r'(?m)^set_pixel_adj:\n.*?; End of function set_pixel_adj\r?\n', N_SET_PIXEL_ADJ, 'set_pixel_adj')
    src = _sub(src, r'ret_from_tbl_jp:\n.*?(?=loc_B000:)', N_RET_FROM_TBL, 'ret_from_tbl_jp')
    src, n = re.subn(r'(jump_to_upd_object:[^\n]*\n)\t+ld      l, \(ix\+0\)\r?\n', lambda m: m.group(1) + "\t\t\t\tld      hl, (z80_ix)\n\t\t\t\tld      l, (hl)\n", src, count=1)
    assert n == 1, "jump_to_upd_object" 
    return src
