# -*- coding: utf-8 -*-
"""Нативные i8080-процедуры Knight Lore (logic): применяются к транслируемому тексту после kl_native.apply.
Метод: спецификация на Python (spec/kl_spec_logic.py) -> дамп вызовов (tools/calldump_vec.js) -> asm; см. docs/PORT_RECIPE.md."""
import re

SCRATCH = ""   # ячейки нулевой страницы (DS), добавляются после kl_native.SCRATCH

def apply(src):
    return src
