# html_cleaner.py (moved into core)
from bs4 import BeautifulSoup, Tag
import json
import re
from pathlib import Path

from core.config import PROJECT_ROOT


def clean_html_tables(html: str) -> str:
    soup = BeautifulSoup(html, 'html.parser')
    remove_duplicate_tables(soup)
    for table in soup.find_all('table'):
        clean_table(table, soup)
    return str(soup)


def extract_candidate_products(html: str, limit: int = 50) -> list[str]:
    return []


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


def _looks_like_header_label(s: str) -> bool:
    if not s:
        return False
    return any(k in s for k in ('код', 'позиция', 'единица', 'измерение', 'цена', 'сумма', 'количество', 'производитель', 'характеристика', 'описание', 'комментарий'))


def _is_likely_header_label(s: str) -> bool:
    return _looks_like_header_label(_normalize_header_text(s))


def _extract_candidate_from_header_cell(s: str) -> str | None:
    if not s:
        return None
    s = s.strip()
    if len(s.split()) <= 6 and not _is_likely_header_label(s):
        return s
    return None


def _row_looks_like_metadata(row: list[str]) -> bool:
    joined = ' '.join(row).lower()
    return any(k in joined for k in ('цена', 'количество', 'сумма', 'руб', 'итого', 'единица'))


_PRODUCT_TERMS = None
_PRODUCT_TERM_ALIASES = None


def _normalize_text(s: str) -> str:
    if not s:
        return ''
    return re.sub(r'[^\w\dа-яё]+', ' ', s.lower()).strip()


def _load_product_terms() -> list[dict]:
    global _PRODUCT_TERMS, _PRODUCT_TERM_ALIASES
    if _PRODUCT_TERMS is not None:
        return _PRODUCT_TERMS

    _PRODUCT_TERMS = []
    _PRODUCT_TERM_ALIASES = []
    terms_path = PROJECT_ROOT / 'data' / 'product_terms.json'
    try:
        with open(terms_path, 'r', encoding='utf-8') as f:
            _PRODUCT_TERMS = json.load(f)
    except Exception:
        _PRODUCT_TERMS = []
        _PRODUCT_TERM_ALIASES = []
        return _PRODUCT_TERMS

    for term in _PRODUCT_TERMS:
        name = term.get('name', '').strip()
        if name:
            alias = _normalize_text(name)
            if alias:
                _PRODUCT_TERM_ALIASES.append((alias, term))
        for raw_alias in term.get('aliases', []):
            alias = _normalize_text(raw_alias)
            if alias:
                _PRODUCT_TERM_ALIASES.append((alias, term))

    # Sort by alias length so longer alias phrases match first
    _PRODUCT_TERM_ALIASES.sort(key=lambda item: -len(item[0]))
    return _PRODUCT_TERMS


def _match_product_term(s: str) -> dict | None:
    if not s:
        return None
    _load_product_terms()
    norm = _normalize_text(s)
    if not norm or not _PRODUCT_TERM_ALIASES:
        return None

    for alias, term in _PRODUCT_TERM_ALIASES:
        if alias == norm:
            return term
        if f' {alias} ' in f' {norm} ':
            return term
    return None


def _candidate_matches_product_terms(s: str) -> bool:
    if not s:
        return False
    term = _match_product_term(s)
    return term is not None


def _canonicalize_candidate(s: str) -> str:
    return ' '.join((s or '').strip().split())


def _normalize_candidate_name(s: str) -> str:
    return ' '.join((s or '').strip().split())
