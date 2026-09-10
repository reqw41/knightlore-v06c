# -*- coding: utf-8 -*-
"""Нативные i8080-процедуры Knight Lore (render): применяются к транслируемому тексту после kl_native.apply.
Метод: спецификация на Python (spec/kl_spec_render.py) -> дамп вызовов (tools/calldump_vec.js) -> asm; см. docs/PORT_RECIPE.md."""
import re

SCRATCH = ""   # ячейки нулевой страницы (DS), добавляются после kl_native.SCRATCH


# ---------------------------------------------------------------------------
# save_2d_info -> встроен в update_sprite_loop (единственный вызыватель).
# Было: call(17) + тело kl_native(204) + jump_to_upd_object `ld hl,(z80_ix)/ld l,(hl)`(23) = 244 такта.
# Стало: один проход по HL (obj+24 -> obj+28), graphic_no прочитан заодно в B: 149 тактов.
# BC/DE больше не сохраняются (в оригинале save_2d_info их берёг): у входа в обработчик DE и так
# был мусором предыдущего обработчика; проверено локстепом 600 и диффом буфера/экрана.
# jump_to_upd_object остаётся отдельной точкой входа (на неё прыгают из upd_player_*), её тело
# переезжает за `jp (hl)` и возвращается в общий хвост `ld bc, upd_sprite_jmp_tbl`.
# ---------------------------------------------------------------------------
N_USL_INLINE = """\t\t\t\t; --- save_2d_info встроен: obj[28..31] = obj[24..27], graphic_no -> B ---
				ld		hl, (z80_ix)
				ld		b, (hl)								; graphic no
				ld		de, 24
				add		hl, de								; -> obj+24 (перенос возможен: obj&31 = 8)
				ld		c, (hl)
				inc		hl
				ld		d, (hl)
				inc		hl
				ld		e, (hl)
				inc		hl
				ld		a, (hl)
				inc		hl									; -> obj+28
				ld		(hl), c
				inc		hl
				ld		(hl), d
				inc		hl
				ld		(hl), e
				inc		hl
				ld		(hl), a
				ld		l, b								; graphic no -> L (как ld l,(ix+0))

usl_tbl:		ld      bc, upd_sprite_jmp_tbl

jump_to_tbl_entry:
"""

N_JUMP_TO_UPD = """
jump_to_upd_object:                                             ; sprite graphic to render (вход извне)
				ld		hl, (z80_ix)
				ld		l, (hl)
				jp		usl_tbl
"""


def _sub(src, pattern, repl, name, flags=re.S):
    src, n = re.subn(pattern, lambda m: repl, src, count=1, flags=flags)
    assert n == 1, "kl_native_render: не найдено: " + name
    return src


def apply(src):
    # 1. встроить save_2d_info в update_sprite_loop и увести jump_to_upd_object за jp (hl)
    src = _sub(src,
               r'\t+call    save_2d_info\r?\n+jump_to_upd_object:[^\n]*\r?\n'
               r'\t+ld      hl, \(z80_ix\)\r?\n\t+ld      l, \(hl\)\r?\n'
               r'\t+ld      bc, upd_sprite_jmp_tbl\r?\n+jump_to_tbl_entry:\r?\n',
               N_USL_INLINE, 'save_2d_info -> update_sprite_loop')
    src = _sub(src, r'(?m)^\t+jp      \(hl\)[ \t]+; go\r?\n',
               "\t\t\t\tjp      (hl)                                    ; go\n" + N_JUMP_TO_UPD,
               'перенос jump_to_upd_object')
    # 2. тело save_2d_info больше никому не нужно
    src = _sub(src, r'(?m)^save_2d_info:.*?; End of function save_2d_info\r?\n', '', 'снять save_2d_info')
    return src
