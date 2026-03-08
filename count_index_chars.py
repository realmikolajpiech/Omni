#!/usr/bin/env python3
"""
count_index_chars.py — Zlicza całkowitą liczbę znaków wszystkich plików,
które zostałyby zaindeksowane przez model embeddingowy (content indexing).

Używa tych samych filtrów co indexer.py:
  - _collect_files() — skanowanie i filtry katalogów/rozszerzeń
  - _static_skip() — pomija boilerplate
  - process_file_content() — ekstrakcja tekstu (PDF, DOCX, XLSX, PPTX, CSV, txt...)

Uruchomienie:
    python count_index_chars.py
    python count_index_chars.py --verbose   # pokazuje każdy plik
"""

import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.core.config import (
    HOME, IGNORE_DIRS, BLOCKED_EXTENSIONS, BLOCKED_FILENAMES,
    CONTENT_SKIP_FILENAMES, CONTENT_SKIP_DIRS,
    CONTENT_SKIP_SUFFIXES, CONTENT_SKIP_EXTENSIONS,
)
from src.services.search.utils import process_file_content, is_text_file


VERBOSE = "--verbose" in sys.argv


def _elapsed(start: float) -> str:
    s = int(time.time() - start)
    return f"{s}s" if s < 60 else f"{s // 60}m {s % 60}s"


def _collect_text_files(base_dir):
    text_files = []
    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [
            d for d in dirs
            if d not in IGNORE_DIRS
            and not d.startswith(".")
            and not any(d.lower().endswith(ext) for ext in BLOCKED_EXTENSIONS)
        ]
        for file in files:
            if file.startswith("."):
                continue
            if file in BLOCKED_FILENAMES:
                continue
            if any(file.lower().endswith(ext) for ext in BLOCKED_EXTENSIONS):
                continue
            full_path = os.path.join(root, file)
            if is_text_file(full_path):
                text_files.append((full_path, file))
    return text_files


def _static_skip(full_path, filename):
    _, ext = os.path.splitext(filename)
    return (
        filename in CONTENT_SKIP_FILENAMES
        or set(full_path.split(os.sep)) & CONTENT_SKIP_DIRS
        or any(filename.endswith(s) for s in CONTENT_SKIP_SUFFIXES)
        or ext.lower() in CONTENT_SKIP_EXTENSIONS
    )


def main():
    print(f"Skanowanie: {HOME}")
    print("(To może chwilę potrwać...)\n")

    t0 = time.time()

    text_files = _collect_text_files(HOME)
    print(f"Znalezione pliki tekstowe: {len(text_files):,}  ({_elapsed(t0)})")

    total_chars = 0
    total_files = 0
    skipped = 0
    errors = 0

    t1 = time.time()
    for i, (full_path, filename) in enumerate(text_files):
        if _static_skip(full_path, filename):
            skipped += 1
            continue

        try:
            chunks = process_file_content(full_path, chunk_size=512)
            if not chunks:
                skipped += 1
                continue

            chars = sum(len(c) for c in chunks)
            total_chars += chars
            total_files += 1

            if VERBOSE:
                print(f"  {chars:>10,} chars  {full_path}")

            if i % 500 == 0 and i > 0:
                pct = 100.0 * i / len(text_files)
                print(f"  Postęp: {i:,}/{len(text_files):,} ({pct:.1f}%)  "
                      f"łącznie: {total_chars:,} znaków  ({_elapsed(t1)})")

        except Exception as e:
            errors += 1
            if VERBOSE:
                print(f"  BŁĄD: {full_path}: {e}")

    print()
    print("=" * 60)
    print(f"  WYNIKI")
    print("=" * 60)
    print(f"  Pliki z treścią (zaindeksowane):  {total_files:,}")
    print(f"  Pliki pominięte (boilerplate):    {skipped:,}")
    print(f"  Błędy odczytu:                    {errors:,}")
    print(f"  CAŁKOWITA LICZBA ZNAKÓW:          {total_chars:,}")
    print(f"  Szacowane tokeny (~4 znaki/token): {total_chars // 4:,}")
    print(f"  Czas skanowania:                  {_elapsed(t0)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
