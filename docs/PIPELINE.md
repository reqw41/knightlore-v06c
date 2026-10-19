# Конвейер сборки и склейка (бывший README)

> Перенесено из README.md 2026-10-19 без изменений по существу: README теперь — страница
> для игрока. Журнал порта со всеми числами — [PORT_RECIPE.md](PORT_RECIPE.md).


Порт Knight Lore (Ultimate, ZX Spectrum 1984) на ванильный Вектор-06Ц через source-level
рекомпиляцию Z80 → i8080. Разведка, CPC-палитры, профили и ZX-стенд — в `~/projects/knightlore-research`.

## Конвейер
```
src/knightlore_z80.asm  — дизассемблер ZX (tcdev v1.00rc7 + правки jonsole), Z80, sjasmplus
      │  tools/kl_z80_to_8080.py  (ядро из kvalley-v06c/tools/kv_z80_to_8080.py, тег asm-freeze + склейка KL)
      ▼
build/knightlore_8080.asm — та же программа в подмножестве i8080 (мнемоники Z80)
      │  tools/sjasmplus → .r0m (64К, PC=0) + .lst + .sld
      ▼
tools/check_i8080.py      — ГЕЙТ: ни одного Z80-only опкода в листинге (08/10/18/2x/3x/CB/D9/DD/ED/FD)
tools/lockstep_vec.js     — ГЕЙТ: локстеп с оригиналом на headless-ядре vector06js
tools/flipcheck_vec.js    — ГЕЙТ: разворот спрайтов (все спрайты × 4 ориентации, сверка с моделью
                            на JS по возвращённому DE). Единственный гейт, трогающий V-разворот:
                            в сценарных комнатах его нет. Запуск: node tools/flipcheck_vec.js
                            build/knightlore_vec.r0m build/knightlore_vec.sld 3
tools/flipstat_vec.js     — статистика разворотов и симулятор кэша развёрнутых копий
```
`./build.sh [lockstep]` делает всё. Цель `lockstep` — логика 1:1, вывод заглушен (см. ниже).

## Что делает склейка (kl_glue) для цели lockstep
- Раскладка (код после трансляции 47.6К против 30.7К): boot 0x0000, scratch 0x0040–0x0061, ldir-хелперы
  0x0080, код+данные с 0x0100 (~0xBA00), стек 0xBC00–0xBFFF, переменные 0xC000–0xC567, vidbuf 0xC600,
  zx_vram 0xDE00 (= shift_bits_table, без читателей), zx_aram 0xF600, reverse_bits_table 0xF900.
- `read_port` → виртуальная клавиатура `vkbd` (8 рядов ZX-матрицы, активный ноль), тот же контракт.
- Стабы вывода: пиксельный цикл `print_sprite`, `blit_to_screen`, `vidbuf_fill/clear_window`
  (самомодифицирующиеся djnz jonsole). Заголовок print_sprite (ix+24/25) сохранён — его читает логика.
- Самомодифицирующийся код ЛОГИКИ переписан на переменные: мяч (`kl_ball_cc`), движущиеся блоки
  (`kl_blk_off`), `flag_room_visited` (SET n,(HL) как данные → маска сдвигом).
- `font` как ограничитель таблицы объектов → `clear_end` (3 сайта).
- `.copy_loop`: как в оригинале 0xD666 счётчик `objs_wiped_cnt` декрементируется в памяти (jonsole считал в AF').
- seed_3 += mem[seed_2] + L + H: игра читает байт памяти по адресу-счётчику (на ZX это экран) —
  байт подаёт стенд через `kl_rnd_feed` из журнала ZX (только lockstep).
- Добавлено в ядро: `ld (nn),sp` / `ld sp,(nn)`.

## Локстеп
ZX-сторона: `~/projects/knightlore-research/zx-stand/LOCKSTEP.md` (детерминированный снимок без `ld a,r`,
сиды 0x1234/0x5678/0x9A, журнал 0x5BA0–0x6107 на каждой итерации, ввод по итерациям).
Сторона Вектора: `node tools/lockstep_vec.js build/knightlore_8080.r0m build/knightlore_8080.sld 600 <inputs.txt> <zx_states.bin>`.
Точка снятия — запись старшего байта seed_2 в вершине главного цикла, ДО записи (обе стороны).
Сравниваются все переменные, кроме 5 указателей (tmp_SP, gfxbase_8x8, tmp_objects_to_draw, render_obj_1/2).
Ввод на стенде MAME виден игре с итерации N+1 (латч порта по кадру) — `KL_LAG=1` по умолчанию.

**2026-09-09: 600 итераций сценария inputs_alive — совпадение байт в байт.**

## Грабли
- Файл jonsole с CRLF в его правках — регулярки через `\r?\n`.
- Scratch-область и код хелперов не должны пересекаться (пересечение дало «случайное» расхождение).
- Темп на i8080 в стенде: ~7 итераций/с в стартовой комнате (ZX 20) при заглушенном рендере — из-за
  IX-эмуляции в логике; целевая переписка сортировки/логики объектов — следующий этап.
