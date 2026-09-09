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
				ret
; End of function chk_plyr_spec_near_arch
"""


def _sub(src, pattern, repl, name, flags=re.S):
    src, n = re.subn(pattern, lambda m: repl, src, count=1, flags=flags)
    assert n == 1, "kl_native_logic: не найдено: " + name
    return src


def apply(src):
    src = _sub(src, r'(?m)^is_near_to:\n.*?; End of function is_near_to\r?\n', N_IS_NEAR_TO, 'is_near_to')
    src = _sub(src, r'(?m)^chk_plyr_spec_near_arch:\n.*?; End of function chk_plyr_spec_near_arch\r?\n',
               N_CHK_NEAR_ARCH, 'chk_plyr_spec_near_arch')
    return src
