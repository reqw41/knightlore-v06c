# -*- coding: utf-8 -*-
"""Нативные i8080-процедуры Knight Lore (render): применяются к транслируемому тексту после kl_native.apply.
Метод: спецификация на Python (spec/kl_spec_render.py) -> дамп вызовов (tools/calldump_vec.js) -> asm; см. docs/PORT_RECIPE.md."""
import re

SCRATCH = "ps_cwt:\t\t\tDS\t\t1\t\t\t; ширина после клипа по столбцу 31 (редкая ветка ps_cw)\n"   # ячейки нулевой страницы (DS), добавляются после kl_native.SCRATCH


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


# ---------------------------------------------------------------------------
# Заголовок print_sprite (диспетчер входа) на HL-указателе.
# Транслятор разворачивает КАЖДЫЙ (ix+n) в ~13 инструкций (~30 байт, ~90 тактов): сохранение
# HL/A, ld hl,(z80_ix), 16-битное сложение со смещением, обратная раскладка. В заголовке таких
# обращений девять (24, 25 x4, 26 x2, 27 x2) — это ~350 байт и ~1200 тактов на вызов.
# Здесь HL один раз наводится на obj+26 и ходит по полям inc/dec hl, а страница таблицы сдвига
# (единственное, что раньше занимало H с самого начала) кладётся в H последней инструкцией
# перед пиксельным циклом — через самомодифицируемый иммедиат ps_shpg.
# Порядок обращений к полям и все флаги сохранены: выровненный путь H не читает (цепочка
# loc_D790 работает через BC), поэтому отложенная запись H ничего не меняет.
# ---------------------------------------------------------------------------
N_PRINT_SPRITE_HDR = """print_sprite:
				call    flip_sprite
				ld		hl, (z80_ix)
				ld		bc, 26
				add		hl, bc							; HL -> obj+26 (pixel X)
				ld		c, (hl)							; C = pixel X (для адреса в буфере)
				ld		a, c
				and		7								; сдвиг внутри байта?
				jp		z, ps_al
				rlca
				and		0xE
				or		0xF0
				ld		(ps_shpg+1), a					; страница таблицы сдвига -> H перед циклом
				ld		a, (de)
				inc		de
				and		7
				inc		a								; ширина в байтах
				ld		b, a
				dec		hl
				dec		hl								; -> obj+24
				ld		(hl), a							; width_bytes: ширина ДО клипа (по ней стирают, стирание клипуется само)
				call	ps_cw							; B = w' (клип по столбцу 31)
				ld		a, b
				dec		a
				and		7
				add		a, a
				add		a, a
				add		a, a
				add		a, a
				neg
				add		a, 0x50
				jp		ps_ent
ps_al:			ld		(ps_shpg+1), a					; сюда пришли по jp z: A = 0. Выровненная цепочка H не читает,
												;   а vec_ps_budget по нулю узнаёт выровненный путь
				ld		a, (de)
				inc		de
				and		0xF
				ld		b, a							; ширина
				dec		hl
				dec		hl								; -> obj+24
				ld		(hl), a							; width_bytes: ширина ДО клипа
				call	ps_cw							; B = w' (клип по столбцу 31)
				ld		a, b
				add		a, a
				add		a, a
				add		a, a
				sub		b								; 7w: группа выровненной цепочки — 7 байт
				neg
				sub		6
ps_ent:			add		a, low (loc_D7AC+3)				; i8080: jp — младший байт адреса входа
				ld		(loc_D7AC+1), a
				ld		a, b
				dec		a								; столбцовый буфер: откат B на w-1
				ld		(loc_D800+1), a
				inc		hl								; -> obj+25
				ld		a, (de)
				inc		de
				ld		(hl), a							; height_lines
				inc		hl
				inc		hl								; -> obj+27
				add		a, (hl)							; + pixel Y
				ld		b, (hl)							; B = pixel Y (для адреса в буфере)
				dec		hl
				dec		hl								; -> obj+25
				sub		192								; уходит за низ экрана?
				jp		c, ps_pos
				neg
				add		a, (hl)							; + height_lines
				ld		(hl), a							; обрезанная высота
ps_pos:			ld		a, (hl)
				ld		(kl_lines), a					; высота окна (её читает vec_ps_budget)
				call    vidbuf_calc_addr_in_bc			; BC = Y,X -> адрес в буфере; на выходе A = C
				add		a, (hl)							; HL = obj+25 (высота) — процедура HL не трогает
				ld		(ps_end+1), a					; строка за последней: конец пиксельного цикла
				call	vec_ps_budget					; хватит ли кадра на пиксельный цикл под di
				di										; SP уходит в данные спрайта
				ld      (tmp_SP), sp
				ex      de, hl
				ld      sp, hl
				ex      de, hl
ps_shpg:		ld		h, 0							; патч: страница таблицы сдвига
				jp      loc_D7AA

; ---- клип печати по столбцу 31 -------------------------------------------------------------
; Буфер Вектора столбцовый: за столбцом 31 (страница 0xBF) начинается плоскость 2 — селектор
; белого цвета HUD, и любой байт в её строках 32..223 белил бы точку поля. В ZX буфер линейный,
; и перебор заворачивался в левый край следующей строки; здесь его надо обрезать.
; Обе цепочки занимают ровно w столбцов: выровненная делает w групп по столбцу на группу,
; сдвинутая w-1 групп плюс хвостовая запись. Значит w' = min(w, 32 - столбец) и w' >= 1 всегда.
; Вход: C = пиксельный X, B = w. HL (указатель на объект) и DE (данные спрайта) не трогаем.
; Выход: B = w'; конец строки пиксельного цикла -> loc_D7AA (обычно) или ps_fix (при переборе:
; цепочка не дочитает 2*(w-w') байт строки спрайта, ps_fix двигает за них SP). Обе метки лежат
; в одной странице с цепочкой, поэтому патчится один младший байт перехода.
ps_cw:			ld		a, c							; пиксельный X
				cpl
				rrca
				rrca
				rrca
				and		0x1F
				inc		a								; 32 - столбец = сколько столбцов до края буфера
				cp		b
				jp		c, ps_cw_clip
				ld		a, low (loc_D7AA)
ps_cw_set:		ld		(ps_jrow+1), a
				ret
ps_cw_clip:		ld		(ps_cwt), a						; w' = сколько столбцов влезло
				sub		b								; -(перебор)
				add		a, a
				neg										; 2*(w - w') = непрочитанный хвост строки спрайта
				ld		(ps_fix+1), a
				ld		a, (ps_cwt)
				ld		b, a							; B = w'
				ld		a, low (ps_fix)
				jp		ps_cw_set
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
    # 3. заголовок print_sprite (от print_sprite: до конца блока loc_D76F) — на HL-указателе.
    # Только цель vec: у lockstep пиксельный цикл заглушен, kl_lines не существует.
    if "ld      (kl_lines), a" in src:
        src = _sub(src, r'(?m)^print_sprite:.*?jr      loc_D73C\r?\n', N_PRINT_SPRITE_HDR, 'заголовок print_sprite')
    return src
