import os
import shutil
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# ── Fine-grained file classification ──────────────────────────────────────────
# Each category has subcategories for "smart" mode, and a flat label for "type" mode.

FILE_TYPES = {
    # ── Images ──
    ".jpg":  ("Images", "Photos"),
    ".jpeg": ("Images", "Photos"),
    ".png":  ("Images", "Screenshots & Graphics"),
    ".gif":  ("Images", "GIFs"),
    ".bmp":  ("Images", "Other"),
    ".svg":  ("Images", "Vector"),
    ".webp": ("Images", "Photos"),
    ".tiff": ("Images", "Photos"),
    ".ico":  ("Images", "Icons"),
    ".heic": ("Images", "Photos"),
    ".heif": ("Images", "Photos"),
    ".raw":  ("Images", "RAW Photos"),
    ".cr2":  ("Images", "RAW Photos"),
    ".nef":  ("Images", "RAW Photos"),
    ".arw":  ("Images", "RAW Photos"),
    ".dng":  ("Images", "RAW Photos"),
    ".psd":  ("Images", "Design Files"),
    ".ai":   ("Images", "Design Files"),
    ".sketch": ("Images", "Design Files"),
    ".fig":  ("Images", "Design Files"),
    ".xd":   ("Images", "Design Files"),

    # ── Documents ──
    ".pdf":  ("Documents", "PDFs"),
    ".doc":  ("Documents", "Word Documents"),
    ".docx": ("Documents", "Word Documents"),
    ".xls":  ("Documents", "Spreadsheets"),
    ".xlsx": ("Documents", "Spreadsheets"),
    ".numbers": ("Documents", "Spreadsheets"),
    ".ppt":  ("Documents", "Presentations"),
    ".pptx": ("Documents", "Presentations"),
    ".key":  ("Documents", "Presentations"),
    ".txt":  ("Documents", "Text Files"),
    ".md":   ("Documents", "Notes & Markdown"),
    ".rtf":  ("Documents", "Text Files"),
    ".csv":  ("Documents", "Spreadsheets"),
    ".odt":  ("Documents", "Word Documents"),
    ".ods":  ("Documents", "Spreadsheets"),
    ".odp":  ("Documents", "Presentations"),
    ".pages": ("Documents", "Word Documents"),
    ".tex":  ("Documents", "LaTeX"),
    ".log":  ("Documents", "Logs"),

    # ── Archives ──
    ".zip":  ("Archives", None),
    ".rar":  ("Archives", None),
    ".7z":   ("Archives", None),
    ".tar":  ("Archives", None),
    ".gz":   ("Archives", None),
    ".bz2":  ("Archives", None),
    ".xz":   ("Archives", None),
    ".iso":  ("Archives", "Disk Images"),
    ".dmg":  ("Archives", "Disk Images"),
    ".pkg":  ("Archives", "Installers"),
    ".deb":  ("Archives", "Installers"),
    ".rpm":  ("Archives", "Installers"),

    # ── Audio ──
    ".mp3":  ("Audio", "Music"),
    ".wav":  ("Audio", "Lossless"),
    ".flac": ("Audio", "Lossless"),
    ".m4a":  ("Audio", "Music"),
    ".aac":  ("Audio", "Music"),
    ".ogg":  ("Audio", "Music"),
    ".wma":  ("Audio", "Music"),
    ".aiff": ("Audio", "Lossless"),
    ".opus": ("Audio", "Music"),
    ".mid":  ("Audio", "MIDI"),
    ".midi": ("Audio", "MIDI"),

    # ── Video ──
    ".mp4":  ("Video", None),
    ".mkv":  ("Video", None),
    ".avi":  ("Video", None),
    ".mov":  ("Video", None),
    ".webm": ("Video", None),
    ".flv":  ("Video", None),
    ".wmv":  ("Video", None),
    ".m4v":  ("Video", None),
    ".ts":   ("Video", None),  # Note: .ts also used for TypeScript, handled by context

    # ── Code ──
    ".py":   ("Code", "Python"),
    ".pyw":  ("Code", "Python"),
    ".ipynb": ("Code", "Python"),
    ".js":   ("Code", "JavaScript"),
    ".jsx":  ("Code", "JavaScript"),
    ".mjs":  ("Code", "JavaScript"),
    ".cjs":  ("Code", "JavaScript"),
    # .ts conflicts with video; handled in _classify_file
    ".tsx":  ("Code", "TypeScript"),
    ".html": ("Code", "Web"),
    ".htm":  ("Code", "Web"),
    ".css":  ("Code", "Web"),
    ".scss": ("Code", "Web"),
    ".sass": ("Code", "Web"),
    ".less": ("Code", "Web"),
    ".vue":  ("Code", "Web"),
    ".svelte": ("Code", "Web"),
    ".java": ("Code", "Java"),
    ".jar":  ("Code", "Java"),
    ".cpp":  ("Code", "C & C++"),
    ".c":    ("Code", "C & C++"),
    ".h":    ("Code", "C & C++"),
    ".hpp":  ("Code", "C & C++"),
    ".cc":   ("Code", "C & C++"),
    ".json": ("Code", "Data & Config"),
    ".xml":  ("Code", "Data & Config"),
    ".yaml": ("Code", "Data & Config"),
    ".yml":  ("Code", "Data & Config"),
    ".toml": ("Code", "Data & Config"),
    ".ini":  ("Code", "Data & Config"),
    ".cfg":  ("Code", "Data & Config"),
    ".conf": ("Code", "Data & Config"),
    ".env":  ("Code", "Data & Config"),
    ".sql":  ("Code", "SQL"),
    ".php":  ("Code", "PHP"),
    ".rb":   ("Code", "Ruby"),
    ".go":   ("Code", "Go"),
    ".rs":   ("Code", "Rust"),
    ".swift": ("Code", "Swift"),
    ".kt":   ("Code", "Kotlin"),
    ".kts":  ("Code", "Kotlin"),
    ".sh":   ("Code", "Shell"),
    ".bash": ("Code", "Shell"),
    ".zsh":  ("Code", "Shell"),
    ".fish": ("Code", "Shell"),
    ".ps1":  ("Code", "Shell"),
    ".r":    ("Code", "R"),
    ".R":    ("Code", "R"),
    ".m":    ("Code", "MATLAB / Objective-C"),
    ".lua":  ("Code", "Lua"),
    ".dart": ("Code", "Dart"),
    ".scala": ("Code", "Scala"),
    ".ex":   ("Code", "Elixir"),
    ".exs":  ("Code", "Elixir"),
    ".zig":  ("Code", "Zig"),
    ".v":    ("Code", "V / Verilog"),
    ".proto": ("Code", "Data & Config"),
    ".graphql": ("Code", "Data & Config"),
    ".gql":  ("Code", "Data & Config"),
    ".cmake": ("Code", "Build"),
    ".makefile": ("Code", "Build"),
    ".gradle": ("Code", "Build"),
    ".dockerfile": ("Code", "Build"),

    # ── Executables ──
    ".app":  ("Applications", None),
    ".exe":  ("Executables", None),
    ".msi":  ("Executables", "Installers"),
    ".bat":  ("Executables", "Scripts"),
    ".bin":  ("Executables", None),

    # ── Books ──
    ".epub": ("Books", None),
    ".mobi": ("Books", None),
    ".azw3": ("Books", None),
    ".azw":  ("Books", None),
    ".fb2":  ("Books", None),
    ".djvu": ("Books", None),

    # ── Fonts ──
    ".ttf":  ("Fonts", None),
    ".otf":  ("Fonts", None),
    ".woff": ("Fonts", None),
    ".woff2": ("Fonts", None),

    # ── 3D & CAD ──
    ".stl":  ("3D Models", None),
    ".obj":  ("3D Models", None),
    ".fbx":  ("3D Models", None),
    ".blend": ("3D Models", None),
    ".step": ("3D Models", "CAD"),
    ".stp":  ("3D Models", "CAD"),
    ".iges": ("3D Models", "CAD"),

    # ── Databases ──
    ".db":   ("Data", "Databases"),
    ".sqlite": ("Data", "Databases"),
    ".sqlite3": ("Data", "Databases"),
    ".mdb":  ("Data", "Databases"),
    ".parquet": ("Data", "Datasets"),
    ".feather": ("Data", "Datasets"),
    ".arrow": ("Data", "Datasets"),
    ".hdf5": ("Data", "Datasets"),
    ".h5":   ("Data", "Datasets"),
    ".npy":  ("Data", "Datasets"),
    ".npz":  ("Data", "Datasets"),
    ".pickle": ("Data", "Datasets"),
    ".pkl":  ("Data", "Datasets"),

    # ── Torrents ──
    ".torrent": ("Torrents", None),
}

# ── Smart subcategory consolidation ───────────────────────────────────────────
# In "smart" mode, subcategories with fewer files than this threshold
# get merged into the parent category to avoid clutter.
_SMART_MIN_FILES = 3


def _classify_file(filename: str, ext: str) -> tuple[str, str | None] | None:
    """Return (category, subcategory) for a file, or None if unknown."""
    # Handle .ts ambiguity: if file looks like TypeScript (not a video transport stream)
    if ext == ".ts":
        # Video .ts files are usually large or have numeric names (00001.ts)
        # TypeScript files usually have readable names
        name_lower = os.path.splitext(filename)[0].lower()
        if name_lower.isdigit() or name_lower.startswith("segment"):
            return ("Video", None)
        return ("Code", "TypeScript")

    # Handle special filename patterns
    name_lower = filename.lower()
    if name_lower == "makefile" or name_lower == "dockerfile" or name_lower == "jenkinsfile":
        return ("Code", "Build")
    if name_lower == "license" or name_lower.startswith("license."):
        return ("Documents", "Text Files")
    if name_lower == "readme" or name_lower.startswith("readme."):
        return ("Documents", "Notes & Markdown")

    return FILE_TYPES.get(ext)


def organize_folder(path: str, strategy: str = "smart") -> str:
    """
    Organize files in a folder into subfolders based on a strategy.

    Strategies:
        - "smart": Groups by category with intelligent subcategories.
          Subcategories with fewer than 3 files are merged up into the
          parent category to avoid creating too many tiny folders.
        - "type": Groups by broad category only (Images, Documents, etc.).
        - "date": Groups by Year/Month based on modification time.

    Returns a structured summary string.
    """
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return f"Error: Path '{path}' does not exist."
    if not os.path.isdir(path):
        return f"Error: '{path}' is not a directory."

    # ── Phase 1: Scan and classify all files ──────────────────────────────
    files_to_move: list[tuple[str, str, str]] = []  # (src_path, filename, target_folder)
    unrecognized: list[str] = []  # filenames that couldn't be classified
    # For smart mode: track counts per category/subcategory
    sub_counts: dict[str, dict[str, list[str]]] = {}  # cat -> sub -> [filenames]

    for entry in os.scandir(path):
        if entry.is_dir() or entry.name.startswith('.'):
            continue

        filename = entry.name
        file_path = entry.path
        _, ext = os.path.splitext(filename)
        ext = ext.lower()

        if strategy == "date":
            mtime = os.path.getmtime(file_path)
            dt = datetime.fromtimestamp(mtime)
            target = os.path.join(str(dt.year), dt.strftime("%B"))  # e.g. 2025/March
            files_to_move.append((file_path, filename, target))

        elif strategy in ("type", "smart"):
            classification = _classify_file(filename, ext)
            if classification is None:
                # Move unrecognized files to "Other" instead of leaving them
                unrecognized.append(filename)
                files_to_move.append((file_path, filename, "Other"))
                continue

            category, subcategory = classification

            if strategy == "type":
                files_to_move.append((file_path, filename, category))
            else:
                # Smart mode: collect for consolidation pass
                sub_counts.setdefault(category, {})
                sub = subcategory or "__root__"
                sub_counts[category].setdefault(sub, []).append(file_path)

    # ── Phase 1b (smart only): Consolidate small subcategories ────────────
    if strategy == "smart":
        for category, subs in sub_counts.items():
            # If there's only one subcategory or only __root__, just use category
            real_subs = {k: v for k, v in subs.items() if k != "__root__"}

            if not real_subs:
                # No subcategories, just the root
                for fp in subs.get("__root__", []):
                    fn = os.path.basename(fp)
                    files_to_move.append((fp, fn, category))
                continue

            # Count total files in this category
            total = sum(len(v) for v in subs.values())

            # If total files in category is small, don't subcategorize
            if total < _SMART_MIN_FILES * 2:
                for sub_files in subs.values():
                    for fp in sub_files:
                        fn = os.path.basename(fp)
                        files_to_move.append((fp, fn, category))
                continue

            # Merge small subcategories into the parent
            for sub, sub_files in subs.items():
                if sub == "__root__" or len(sub_files) < _SMART_MIN_FILES:
                    # Merge into parent category (no subfolder)
                    for fp in sub_files:
                        fn = os.path.basename(fp)
                        files_to_move.append((fp, fn, category))
                else:
                    # Keep as subcategory
                    for fp in sub_files:
                        fn = os.path.basename(fp)
                        files_to_move.append((fp, fn, os.path.join(category, sub)))

    if not files_to_move:
        return f"No files to organize in '{os.path.basename(path)}'. The folder may already be organized or only contains folders/hidden files."

    # ── Phase 2: Move files ───────────────────────────────────────────────
    moved_count = 0
    created_folders: set[str] = set()
    errors: list[str] = []
    # Track what went where for the summary
    folder_counts: dict[str, int] = {}

    for file_path_src, filename, target_folder_name in files_to_move:
        target_dir = os.path.join(path, target_folder_name)
        if not os.path.exists(target_dir):
            try:
                os.makedirs(target_dir)
                created_folders.add(target_folder_name)
            except OSError as e:
                errors.append(f"Failed to create '{target_folder_name}': {e}")
                continue

        target_path = os.path.join(target_dir, filename)

        # Handle name collision
        if os.path.exists(target_path):
            base, extension = os.path.splitext(filename)
            counter = 1
            while os.path.exists(target_path):
                target_path = os.path.join(target_dir, f"{base}_{counter}{extension}")
                counter += 1

        try:
            shutil.move(file_path_src, target_path)
            moved_count += 1
            folder_counts[target_folder_name] = folder_counts.get(target_folder_name, 0) + 1
        except OSError as e:
            errors.append(f"Failed to move '{filename}': {e}")

    # ── Phase 3: Build detailed summary ───────────────────────────────────
    folder_name = os.path.basename(path)
    lines = [f"Organized {moved_count} files in '{folder_name}':"]

    # Group by top-level category for cleaner output
    top_level: dict[str, list[tuple[str, int]]] = {}
    for folder, count in sorted(folder_counts.items()):
        parts = folder.split(os.sep)
        top = parts[0]
        top_level.setdefault(top, []).append((folder, count))

    for top_cat, entries in sorted(top_level.items()):
        if len(entries) == 1 and entries[0][0] == top_cat:
            lines.append(f"  {top_cat}: {entries[0][1]} files")
        else:
            total = sum(c for _, c in entries)
            lines.append(f"  {top_cat}: {total} files")
            for folder, count in entries:
                sub = folder[len(top_cat)+1:] if os.sep in folder else folder
                if sub != top_cat:
                    lines.append(f"    {sub}: {count}")

    if errors:
        lines.append(f"\n{len(errors)} error(s):")
        for e in errors[:3]:
            lines.append(f"  - {e}")
        if len(errors) > 3:
            lines.append(f"  ... and {len(errors) - 3} more")

    if unrecognized:
        lines.append(f"\nMoved {len(unrecognized)} unrecognized file(s) to 'Other':")
        for fn in unrecognized[:10]:
            lines.append(f"  - {fn}")
        if len(unrecognized) > 10:
            lines.append(f"  ... and {len(unrecognized) - 10} more")

    return "\n".join(lines)
