# html_cleaner.py
"""
Очистка HTML-таблиц от дубликатов и мусора.
"""

from bs4 import BeautifulSoup, Tag
import re


def clean_html_tables(html: str) -> str:
    """
    Основная функция: очищает все таблицы в HTML.
    """
    soup = BeautifulSoup(html, 'html.parser')
    
    # 1. Удаляем дублирующиеся таблицы
    remove_duplicate_tables(soup)
    
    # 2. Обрабатываем каждую таблицу
    for table in soup.find_all('table'):
        clean_table(table, soup)
    
    return str(soup)


def extract_candidate_products(html: str, limit: int = 50) -> list[str]:
    soup = BeautifulSoup(html or '', 'html.parser')
    candidates = []
    seen = set()

    for table in soup.find_all('table'):
        if table.find('table') is not None:
            continue

        grid = _table_to_text_grid(table)
        if not grid:
            continue

        header_rows = _detect_header_rows(grid)
        product_column_indexes = []

        for row_index in header_rows:
            row = grid[row_index]
            for column_index, cell_text in enumerate(row):
                header_text = _normalize_header_text(cell_text)
                if not _is_product_header(header_text):
                    continue
                product_column_indexes.append(column_index)

        for row in grid[:8]:
            for cell_text in row:
                inline_candidate = _extract_candidate_from_header_cell(cell_text)
                if inline_candidate and inline_candidate not in seen:
                    seen.add(inline_candidate)
                    candidates.append(inline_candidate)

        product_column_indexes = list(dict.fromkeys(product_column_indexes))
        if not product_column_indexes:
            continue

        data_start_index = (max(header_rows) + 1) if header_rows else 0
        for row in grid[data_start_index:]:
            if _row_looks_like_metadata(row):
                continue
            for column_index in product_column_indexes:
                if column_index >= len(row):
                    continue
                raw_value = row[column_index]
                if _is_product_header(_normalize_header_text(raw_value)):
                    continue
                value = _normalize_candidate_name(raw_value)
                if not value or value in seen:
                    continue
                seen.add(value)
                candidates.append(value)

        if len(candidates) >= limit:
            break

    return candidates[:limit]


def remove_duplicate_tables(soup: BeautifulSoup):
    """
    Удаляет дублирующиеся таблицы по сигнатуре.
    """
    tables = soup.find_all('table')
    seen = set()
    to_remove = []
    
    for table in tables:
        # Сигнатура: все тексты ячеек (первые 500 символов)
        texts = []
        for cell in table.find_all(['td', 'th']):
            text = cell.get_text(strip=True)
            if text:
                texts.append(text[:50])
        
        signature = '|'.join(texts[:30])
        
        if signature and signature in seen:
            to_remove.append(table)
        elif signature:
            seen.add(signature)
    
    for table in to_remove:
        table.decompose()


def clean_table(table: Tag, soup: BeautifulSoup):
    """
    Очищает одну таблицу:
    - Удаляет пустые строки
    - Удаляет пустые колонки
    - Объединяет дублирующиеся ячейки
    """
    rows = table.find_all('tr')
    if len(rows) < 2:
        return
    
    # 1. Удаляем пустые строки
    for tr in rows:
        if not tr.get_text(strip=True):
            tr.decompose()
    
    # Обновляем rows
    rows = table.find_all('tr')
    if len(rows) < 2:
        return
    
    # 2. Определяем структуру таблицы
    grid, spans = parse_table_grid(rows)
    if not grid:
        return
    
    # 3. Удаляем пустые колонки справа
    keep_rows, keep_cols = find_non_empty_columns(grid)
    
    # Если ничего менять не нужно
    if len(keep_cols) == len(grid[0]) and len(keep_rows) == len(grid):
        # Всё равно объединяем дубликаты
        merge_duplicate_cells(rows)
        return
    
    # 4. Сжимаем таблицу
    new_grid, new_spans = compress_grid(grid, spans, keep_rows, keep_cols, soup)
    
    # 5. Строим новую таблицу
    new_table = grid_to_table(new_grid, new_spans, soup)
    
    # 6. Заменяем
    table.clear()
    for element in new_table:
        table.append(element)


def parse_table_grid(rows) -> tuple:
    """
    Разворачивает таблицу в матрицу с учётом colspan/rowspan.
    """
    grid = []
    spans = {}  # (row, col) -> (rowspan, colspan, cell)
    rowspan_placeholder = {}
    
    for row_idx, tr in enumerate(rows):
        row_cells = []
        col = 0
        
        # Пропускаем ячейки, занятые rowspan
        while (row_idx, col) in rowspan_placeholder:
            row_cells.append(None)
            col += 1
        
        for cell in tr.find_all(['td', 'th'], recursive=False):
            while (row_idx, col) in rowspan_placeholder:
                row_cells.append(None)
                col += 1
            # Ensure we pass a str/int into int() to satisfy type checkers
            colspan = int(str(cell.get('colspan') or 1))
            rowspan = int(str(cell.get('rowspan') or 1))
            
            spans[(row_idx, col)] = (rowspan, colspan, cell)
            row_cells.append(cell)
            
            # Заполняем colspan
            for c in range(1, colspan):
                row_cells.append(None)
                rowspan_placeholder[(row_idx, col + c)] = True
            
            # Регистрируем rowspan
            if rowspan > 1:
                for r in range(1, rowspan):
                    rowspan_placeholder[(row_idx + r, col)] = True
                    for c in range(1, colspan):
                        rowspan_placeholder[(row_idx + r, col + c)] = True
            
            col += colspan
        
        grid.append(row_cells)
    
    # Выравниваем строки
    max_cols = max(len(row) for row in grid) if grid else 0
    for row in grid:
        while len(row) < max_cols:
            row.append(None)
    
    return grid, spans


def find_non_empty_columns(grid) -> tuple:
    """
    Определяет, какие строки и колонки содержат непустые данные.
    """
    if not grid:
        return [], []
    
    rows = len(grid)
    cols = len(grid[0])
    
    has_data_row = [False] * rows
    has_data_col = [False] * cols
    
    for r in range(rows):
        for c in range(cols):
            cell = grid[r][c]
            if cell and cell.get_text(strip=True):
                has_data_row[r] = True
                has_data_col[c] = True
    
    # Находим последнюю колонку с данными
    last_data_col = -1
    for c in range(cols - 1, -1, -1):
        if has_data_col[c]:
            last_data_col = c
            break
    
    if last_data_col == -1:
        return [], []
    
    # Оставляем только колонки до последней с данными
    keep_cols = [c for c in range(last_data_col + 1)]
    keep_rows = [r for r in range(rows) if has_data_row[r]]
    
    return keep_rows, keep_cols


def compress_grid(grid, spans, keep_rows, keep_cols, soup) -> tuple:
    # Note: now expects a BeautifulSoup `soup` so new tags are created in the
    # correct document context.
    """
    Сжимает матрицу, пересчитывая rowspan/colspan.
    """
    if not keep_rows or not keep_cols:
        return [], {}
    
    # Маппинг старых индексов в новые
    row_map = {old: new for new, old in enumerate(keep_rows)}
    col_map = {old: new for new, old in enumerate(keep_cols)}
    
    new_rows = len(keep_rows)
    new_cols = len(keep_cols)
    new_grid = [[None for _ in range(new_cols)] for _ in range(new_rows)]
    new_spans = {}
    
    for (r, c), (rowspan, colspan, cell) in spans.items():
        start_r = r
        end_r = r + rowspan - 1
        start_c = c
        end_c = c + colspan - 1
        
        # Находим видимые строки и колонки
        visible_rows = [i for i in range(start_r, end_r + 1) if i in row_map]
        visible_cols = [i for i in range(start_c, end_c + 1) if i in col_map]
        
        if not visible_rows or not visible_cols:
            continue
        
        new_r = row_map[visible_rows[0]]
        new_c = col_map[visible_cols[0]]
        new_rowspan = len(visible_rows)
        new_colspan = len(visible_cols)
        
        if new_grid[new_r][new_c] is None:
            # Create new tag in the provided soup to avoid cross-document issues
            new_cell = soup.new_tag(cell.name)
            for content in cell.contents:
                if isinstance(content, Tag):
                    try:
                        new_cell.append(content.__copy__())
                    except Exception:
                        # Fallback: parse the content HTML into the current soup
                        new_cell.append(BeautifulSoup(str(content), 'html.parser'))
                else:
                    new_cell.append(content)

            if new_rowspan > 1:
                new_cell.attrs['rowspan'] = str(new_rowspan)
            if new_colspan > 1:
                new_cell.attrs['colspan'] = str(new_colspan)

            new_grid[new_r][new_c] = new_cell
            new_spans[(new_r, new_c)] = (new_rowspan, new_colspan, new_cell)
    
    return new_grid, new_spans


def grid_to_table(grid, spans, soup) -> list:
    """
    Превращает матрицу обратно в HTML-таблицу.
    """
    if not grid:
        return []
    
    rows = []
    rowspan_tracker = {}
    
    for r, row in enumerate(grid):
        tr = soup.new_tag('tr')
        c = 0
        
        while c < len(row):
            while (r, c) in rowspan_tracker:
                c += 1
            
            if c >= len(row):
                break
            
            cell = grid[r][c]
            if cell is None:
                c += 1
                continue
            
            # Очищаем текст от шума
            clean_cell_text(cell)

            # Coerce attribute values to strings before converting to int
            rowspan = int(str(cell.get('rowspan') or 1))
            colspan = int(str(cell.get('colspan') or 1))
            
            tr.append(cell)
            
            if rowspan > 1:
                for dr in range(1, rowspan):
                    for dc in range(colspan):
                        rowspan_tracker[(r + dr, c + dc)] = cell
            
            c += colspan
        
        if tr.find_all():
            rows.append(tr)
    
    return rows


def clean_cell_text(cell: Tag):
    """Очищает текст ячейки от шума."""
    text = cell.get_text(strip=True)
    if not text:
        return
    
    # Удаляем шумные фразы
    noise_phrases = [
        'значение характеристики не может изменяться участником закупки',
        'участник закупки указывает в заявке конкретное значение',
        'дополнительная информация, не предусмотренная',
        'обоснование включения дополнительной информации',
        'с целью точного отражения',
        'необходимы для предотвращения',
        'не влекут за собой ограничения',
        'определяют область применения',
    ]
    
    text_clean = text
    for phrase in noise_phrases:
        text_clean = text_clean.replace(phrase, '')
    
    text_clean = ' '.join(text_clean.split())
    
    # Если слишком длинный
    if len(text_clean) > 300:
        text_clean = text_clean[:250] + "..."
    
    if text_clean and text_clean != text:
        cell.clear()
        cell.string = text_clean


def merge_duplicate_cells(rows):
    """
    Объединяет дублирующиеся ячейки в таблице.
    """
    if not rows:
        return
    
    # === По горизонтали ===
    for row in rows:
        cells = row.find_all(['td', 'th'])
        i = 0
        while i < len(cells) - 1:
            curr = cells[i]
            nxt = cells[i + 1]
            curr_text = curr.get_text(strip=True)
            nxt_text = nxt.get_text(strip=True)
            
            if curr_text and curr_text == nxt_text and len(curr_text) < 200:
                curr_colspan = int(str(curr.get('colspan') or 1))
                nxt_colspan = int(str(nxt.get('colspan') or 1))
                curr.attrs['colspan'] = str(curr_colspan + nxt_colspan)
                nxt.decompose()
                cells = row.find_all(['td', 'th'])
            else:
                i += 1
    
    # === По вертикали ===
    if len(rows) < 2:
        return
    
    max_cols = 0
    for row in rows:
        cols = sum(int(str(cell.get('colspan') or 1)) for cell in row.find_all(['td', 'th']))
        max_cols = max(max_cols, cols)
    
    if max_cols == 0:
        return
    
    for col in range(max_cols):
        col_texts = []
        col_cells = []
        
        for row in rows:
            col_pos = 0
            cell = None
            for c in row.find_all(['td', 'th']):
                colspan = int(str(c.get('colspan') or 1))
                if col_pos <= col < col_pos + colspan:
                    cell = c
                    break
                col_pos += colspan
            
            if cell:
                text = cell.get_text(strip=True)
                col_texts.append(text)
                col_cells.append(cell)
            else:
                col_texts.append(None)
                col_cells.append(None)
        
        i = 0
        while i < len(rows):
            if not col_texts[i]:
                i += 1
                continue
            
            span = 1
            while (i + span < len(rows) and 
                   col_texts[i + span] == col_texts[i] and
                   span < 10):
                span += 1
            
            if span > 1 and col_cells[i] is not None:
                cell = col_cells[i]
                current_rowspan = int(str(cell.get('rowspan') or 1))
                if current_rowspan == 1:
                    cell.attrs['rowspan'] = str(span)
                    for j in range(i + 1, i + span):
                        if col_cells[j] is not None and col_cells[j] != cell:
                            col_cells[j].decompose()
            
            i += span


def _table_to_text_grid(table: Tag) -> list[list[str]]:
    rows = table.find_all('tr')
    active_rowspans = {}
    grid = []

    for row in rows:
        current_row = []
        column_index = 0

        while column_index in active_rowspans:
            span = active_rowspans[column_index]
            current_row.append(span['text'])
            span['remaining'] -= 1
            if span['remaining'] <= 0:
                del active_rowspans[column_index]
            column_index += 1

        for cell in row.find_all(['th', 'td'], recursive=False):
            while column_index in active_rowspans:
                span = active_rowspans[column_index]
                current_row.append(span['text'])
                span['remaining'] -= 1
                if span['remaining'] <= 0:
                    del active_rowspans[column_index]
                column_index += 1

            text = re.sub(r'\s+', ' ', cell.get_text(' ', strip=True))
            rowspan = max(1, int(str(cell.get('rowspan') or 1)))
            colspan = max(1, int(str(cell.get('colspan') or 1)))

            for offset in range(colspan):
                current_row.append(text)
                if rowspan > 1:
                    active_rowspans[column_index + offset] = {
                        'text': text,
                        'remaining': rowspan - 1,
                    }
            column_index += colspan

        grid.append(current_row)

    return grid


def _detect_header_rows(grid: list[list[str]]) -> list[int]:
    header_rows = []
    for row_index, row in enumerate(grid[:4]):
        if any(_is_product_header(_normalize_header_text(cell_text)) for cell_text in row):
            header_rows.append(row_index)
            continue
        if row_index == 0 and any('наименование' in _normalize_header_text(cell_text) for cell_text in row):
            header_rows.append(row_index)
    return header_rows


def _row_looks_like_metadata(row: list[str]) -> bool:
    row_text = _normalize_header_text(' '.join(row))
    metadata_markers = (
        'наименование характеристики',
        'значение характеристики',
        'единица измерения характеристики',
        'инструкция по заполнению',
        'информацию и документы об участнике закупки',
        'предложение участника закупки',
        'данная информация и документы не включаются',
    )
    return any(marker in row_text for marker in metadata_markers)


def _normalize_header_text(text: str) -> str:
    normalized = re.sub(r'\s+', ' ', (text or '').strip().lower())
    return normalized.replace('ё', 'е')


def _is_product_header(header_text: str) -> bool:
    if 'наименование' not in header_text:
        return False
    if 'товар' in header_text:
        return True
    return 'работ' in header_text and 'услуг' in header_text


def _normalize_candidate_name(text: str) -> str:
    value = re.sub(r'\s+', ' ', (text or '').strip())
    if not value:
        return ''

    lowered = value.lower()
    forbidden_markers = (
        'значение характеристики',
        'наименование характеристики',
        'единица измерения характеристики',
        'наименование поставляемого товара',
        'наименование товара, работы, услуги',
        'наименование товара (работы, услуги)',
        'характеристики товара',
        'характеристики товара, работы, услуги',
        'код позиции',
        'цена товара',
        'общая стоимость',
        'информацию и документы об участнике закупки',
        'данная информация и документы не включаются',
        'предложение участника закупки',
    )
    if any(marker in lowered for marker in forbidden_markers):
        return ''
    if value.startswith(('(', ',', ';', ':')):
        return ''
    if re.fullmatch(r'[\d\W_]+', value):
        return ''
    if len(value) < 3 or len(value) > 180:
        return ''
    if len(value.split()) > 18:
        return ''
    return value


def _extract_candidate_from_header_cell(text: str) -> str:
    value = re.sub(r'\s+', ' ', (text or '').strip())
    patterns = [
        r'наименование\s+товара,\s*работы,\s*услуги(?:\s*№\s*\d+)?\s*[:\-]?\s*(.+)$',
        r'наименование\s+(?:поставляемого\s+)?товара(?:\s*\([^)]*\))?(?:\s*№\s*\d+)?\s*[:\-]?\s*(.+)$',
    ]
    for pattern in patterns:
        match = re.search(pattern, value, re.IGNORECASE)
        if not match:
            continue
        candidate = _normalize_candidate_name(match.group(1))
        if candidate:
            return candidate
    return ''