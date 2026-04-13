# html_cleaner.py (moved into core)
from bs4 import BeautifulSoup, Tag
import re


def clean_html_tables(html: str) -> str:
    soup = BeautifulSoup(html, 'html.parser')
    remove_duplicate_tables(soup)
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
    tables = soup.find_all('table')
    seen = set()
    to_remove = []
    
    for table in tables:
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
    rows = table.find_all('tr')
    if len(rows) < 2:
        return
    
    for tr in rows:
        if not tr.get_text(strip=True):
            tr.decompose()
    
    rows = table.find_all('tr')
    if len(rows) < 2:
        return
    
    grid, spans = parse_table_grid(rows)
    if not grid:
        return
    
    keep_rows, keep_cols = find_non_empty_columns(grid)
    
    if len(keep_cols) == len(grid[0]) and len(keep_rows) == len(grid):
        merge_duplicate_cells(rows)
        return
    
    new_grid, new_spans = compress_grid(grid, spans, keep_rows, keep_cols, soup)
    new_table = grid_to_table(new_grid, new_spans, soup)
    table.clear()
    for element in new_table:
        table.append(element)


def parse_table_grid(rows) -> tuple:
    grid = []
    spans = {}
    rowspan_placeholder = {}
    
    for row_idx, tr in enumerate(rows):
        row_cells = []
        col = 0
        
        while (row_idx, col) in rowspan_placeholder:
            row_cells.append(None)
            col += 1
        
        for cell in tr.find_all(['td', 'th'], recursive=False):
            while (row_idx, col) in rowspan_placeholder:
                row_cells.append(None)
                col += 1
            colspan = int(str(cell.get('colspan') or 1))
            rowspan = int(str(cell.get('rowspan') or 1))
            
            spans[(row_idx, col)] = (rowspan, colspan, cell)
            row_cells.append(cell)
            
            for c in range(1, colspan):
                row_cells.append(None)
                rowspan_placeholder[(row_idx, col + c)] = True
            
            if rowspan > 1:
                for r in range(1, rowspan):
                    rowspan_placeholder[(row_idx + r, col)] = True
                    for c in range(1, colspan):
                        rowspan_placeholder[(row_idx + r, col + c)] = True
            
            col += colspan
        
        grid.append(row_cells)
    
    max_cols = max(len(row) for row in grid) if grid else 0
    for row in grid:
        while len(row) < max_cols:
            row.append(None)
    
    return grid, spans


def find_non_empty_columns(grid) -> tuple:
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
    
    last_data_col = -1
    for c in range(cols - 1, -1, -1):
        if has_data_col[c]:
            last_data_col = c
            break
    
    if last_data_col == -1:
        return [], []
    
    keep_cols = [c for c in range(last_data_col + 1)]
    keep_rows = [r for r in range(rows) if has_data_row[r]]
    
    return keep_rows, keep_cols


def compress_grid(grid, spans, keep_rows, keep_cols, soup) -> tuple:
    if not keep_rows or not keep_cols:
        return [], {}
    
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
        
        visible_rows = [i for i in range(start_r, end_r + 1) if i in row_map]
        visible_cols = [i for i in range(start_c, end_c + 1) if i in col_map]
        
        if not visible_rows or not visible_cols:
            continue
        
        new_r = row_map[visible_rows[0]]
        new_c = col_map[visible_cols[0]]
        new_rowspan = len(visible_rows)
        new_colspan = len(visible_cols)
        
        if new_grid[new_r][new_c] is None:
            new_cell = soup.new_tag(cell.name)
            for content in cell.contents:
                if isinstance(content, Tag):
                    new_cell.append(content.__copy__())
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
            
            clean_cell_text(cell)

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
    text = cell.get_text(strip=True)
    if not text:
        return
    
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
    
    if len(text_clean) > 300:
        text_clean = text_clean[:250] + "..."
    
    if text_clean and text_clean != text:
        cell.clear()
        cell.string = text_clean


def merge_duplicate_cells(rows):
    if not rows:
        return
    
    for row in rows:
        cells = row.find_all(['td', 'th'])
        i = 0
        while i < len(cells) - 1:
            curr = cells[i]
            nxt = cells[i + 1]
            curr_text = curr.get_text(strip=True)
            nxt_text = nxt.get_text(strip=True)
            if curr_text and curr_text == nxt_text:
                nxt.decompose()
                cells.pop(i + 1)
            else:
                i += 1


# Heuristic helpers for candidate extraction
def _table_to_text_grid(table):
    rows = table.find_all('tr')
    grid = []
    for tr in rows:
        cells = [td.get_text(separator=' ', strip=True) for td in tr.find_all(['td', 'th'])]
        grid.append(cells)
    return grid


def _detect_header_rows(grid):
    header_rows = []
    for idx, row in enumerate(grid[:6]):
        joined = ' '.join(row).lower()
        if any(k in joined for k in ('наименование', 'наименование товара', 'товар')) or any(len(c) > 30 for c in row):
            header_rows.append(idx)
    return header_rows


def _normalize_header_text(s: str) -> str:
    return re.sub(r'[^\w\dа-яё]+', ' ', (s or '').lower())


def _is_product_header(s: str) -> bool:
    return any(k in s for k in ('наименование', 'товар', 'наим'))


def _extract_candidate_from_header_cell(s: str) -> str | None:
    if not s:
        return None
    s = s.strip()
    if len(s) > 3 and len(s.split()) <= 6:
        return s
    return None


def _row_looks_like_metadata(row: list[str]) -> bool:
    joined = ' '.join(row).lower()
    return any(k in joined for k in ('цена', 'количество', 'сумма', 'руб', 'итого'))


def _normalize_candidate_name(s: str) -> str:
    return s.strip()
