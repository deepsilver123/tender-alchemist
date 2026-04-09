#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🧹 Универсальный конвертер DOCX → Чистый HTML

Конвертирует DOCX в HTML через pandoc и агрессивно очищает от мусора.
Сокращает размер файла в 3-4 раза, сохраняя всю полезную информацию.

Использование:
    python docx_to_clean_html.py input.docx [output.html]

Примеры:
    python docx_to_clean_html.py document.docx
    python docx_to_clean_html.py document.docx clean.html
"""

import subprocess
import sys
import tempfile
from pathlib import Path
from bs4 import BeautifulSoup


def convert_docx_to_html(docx_path: str) -> str:
    """
    Конвертирует DOCX в HTML через pandoc.

    Args:
        docx_path: Путь к DOCX файлу

    Returns:
        HTML-строка или сообщение об ошибке
    """
    file_path = Path(docx_path)
    if not file_path.exists():
        return f"❌ Файл не найден: {docx_path}"

    print(f"📄 Конвертация: {file_path.name}")

    with tempfile.NamedTemporaryFile(suffix='.html', delete=False) as tmp:
        tmp_path = tmp.name

    try:
        # Конвертация через pandoc
        result = subprocess.run(
            ['pandoc', str(file_path), '-o', tmp_path, '--to', 'html', '--wrap=none'],
            check=True,
            capture_output=True,
            text=True,
            timeout=60
        )

        # Читаем результат
        with open(tmp_path, 'r', encoding='utf-8') as f:
            content = f.read()

        return content

    except subprocess.CalledProcessError as e:
        return f"❌ Ошибка pandoc: {e.stderr}"
    except FileNotFoundError:
        return "❌ pandoc не установлен! Установите его: https://pandoc.org/installing.html"
    except subprocess.TimeoutExpired:
        return "❌ Таймаут: pandoc не ответил за 60 секунд"
    except Exception as e:
        return f"❌ Ошибка: {e}"
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def clean_html_aggressive(html: str) -> str:
    """
    Агрессивная очистка HTML от pandoc-мусора.
    Удаляет: colgroup, стили, пустые ячейки, пустые строки, пустые таблицы.

    Args:
        html: HTML-строка для очистки

    Returns:
        Очищенная HTML-строка
    """
    soup = BeautifulSoup(html, 'html.parser')

    # 1. Удаляем colgroup и col элементы
    for elem in soup.find_all('colgroup'):
        elem.decompose()
    for elem in soup.find_all('col'):
        elem.decompose()

    # 2. Удаляем ненужные атрибуты
    attrs_to_remove = {'style', 'width', 'height', 'border', 'cellpadding', 'cellspacing', 'class', 'id'}

    for tag in soup.find_all():
        for attr in list(tag.attrs.keys()):
            if attr in attrs_to_remove:
                del tag[attr]

    # 3. Упрощаем colspan/rowspan
    for tag in soup.find_all(['th', 'td']):
        for attr in ['colspan', 'rowspan']:
            if tag.has_attr(attr):
                try:
                    if int(tag[attr]) == 1:
                        del tag[attr]
                except (ValueError, TypeError):
                    pass

    # 4. Удаляем пустые ячейки из конца строк
    for table in soup.find_all('table'):
        for tr in table.find_all('tr'):
            cells = tr.find_all(['td', 'th'])
            while cells and not cells[-1].get_text(strip=True):
                cells[-1].decompose()
                cells = tr.find_all(['td', 'th'])

    # 5. Удаляем полностью пустые строки
    for table in soup.find_all('table'):
        rows_to_remove = []
        for tr in table.find_all('tr'):
            if not any(cell.get_text(strip=True) for cell in tr.find_all(['td', 'th'])):
                rows_to_remove.append(tr)
        for tr in rows_to_remove:
            tr.decompose()

    # 6. Удаляем полностью пустые таблицы
    for table in list(soup.find_all('table')):
        if not table.get_text(strip=True):
            table.decompose()

    # 7. Нормализуем пробелы
    html_str = str(soup)
    lines = html_str.split('\n')
    result = []
    prev_empty = False

    for line in lines:
        if line.strip():
            result.append(line)
            prev_empty = False
        elif not prev_empty:
            result.append('')
            prev_empty = True

    return '\n'.join(result)


def analyze_html(html: str) -> dict:
    """
    Анализирует HTML и возвращает статистику.

    Args:
        html: HTML-строка

    Returns:
        Словарь со статистикой
    """
    soup = BeautifulSoup(html, 'html.parser')
    tables = soup.find_all('table')

    result = {
        'total_tables': len(tables),
        'table_stats': []
    }

    for i, table in enumerate(tables):
        rows = table.find_all('tr')
        if rows:
            cols = max(len(row.find_all(['td', 'th'])) for row in rows)
            text = table.get_text(strip=True)
            result['table_stats'].append({
                'index': i + 1,
                'rows': len(rows),
                'cols': cols,
                'size': len(text),
                'has_colspan': bool(table.find_all(attrs={'colspan': True})),
                'has_rowspan': bool(table.find_all(attrs={'rowspan': True})),
            })

    return result


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("\n" + "=" * 60)
        print("Примеры:")
        print("  python docx_to_clean_html.py document.docx")
        print("  python docx_to_clean_html.py document.docx output.html")
        print("=" * 60)
        return

    docx_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None

    # Определяем выходной файл
    if output_path:
        output_file = Path(output_path)
    else:
        docx_file = Path(docx_path)
        output_file = docx_file.parent / f"{docx_file.stem}_clean.html"

    print("🚀 DOCX → Чистый HTML Конвертер")
    print("=" * 60)

    # Шаг 1: Конвертация DOCX → HTML
    print("\n[1/3] Конвертация DOCX в HTML через pandoc...")
    html_raw = convert_docx_to_html(docx_path)

    if "❌" in html_raw:
        print(f"\n{html_raw}")
        sys.exit(1)

    size_raw = len(html_raw)
    print(f"   ✓ Сырой HTML: {size_raw:,} байт")

    # Шаг 2: Анализ сырого HTML
    print("\n[2/3] Анализ структуры...")
    stats_raw = analyze_html(html_raw)
    print(f"   ✓ Найдено таблиц: {stats_raw['total_tables']}")

    # Шаг 3: Агрессивная очистка
    print("\n[3/3] Агрессивная очистка HTML...")
    html_clean = clean_html_aggressive(html_raw)

    size_clean = len(html_clean)
    saved = size_raw - size_clean
    saved_pct = 100 * saved / size_raw if size_raw > 0 else 0

    # Анализ очищенного HTML
    stats_clean = analyze_html(html_clean)

    # Сохранение результата
    print(f"\n💾 Сохранение: {output_file}")
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(html_clean)
    except Exception as e:
        print(f"❌ Ошибка сохранения: {e}")
        sys.exit(1)

    # Финальная статистика
    print("\n" + "=" * 60)
    print("✅ Готово! Результаты:")
    print("=" * 60)
    print(f"📁 Входной файл:     {docx_path}")
    print(f"📁 Выходной файл:    {output_file}")
    print()
    print("📊 Статистика конвертации:")
    print(f"   Сырой HTML:       {size_raw:,} байт")
    print(f"   Чистый HTML:      {size_clean:,} байт")
    print(f"   ✂️  Сэкономлено:   {saved:,} байт ({saved_pct:.1f}%)")
    print()
    print("📋 Структура таблиц:")
    print(f"   До очистки:       {stats_raw['total_tables']} таблиц")
    print(f"   После очистки:    {stats_clean['total_tables']} таблиц")
    print(f"   Удалено таблиц:   {stats_raw['total_tables'] - stats_clean['total_tables']}")

    if stats_clean['table_stats']:
        print("\n   Детали таблиц:")
        for stat in stats_clean['table_stats'][:5]:  # Показываем первые 5
            print(f"     Таблица {stat['index']}: {stat['rows']}×{stat['cols']}, "
                  f"{stat['size']} символов")

    print("\n" + "=" * 60)
    print("🎉 Конвертация завершена успешно!")
    print("=" * 60)


if __name__ == "__main__":
    main()
