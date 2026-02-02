# Space-to-Preview Feature Guide

## Overview

Press `SPACE` on any selected file to instantly preview its content. Supports a wide variety of file types with intelligent handling for each type.

## Supported File Types

### Text & Code Files

- `.txt` - Plain text
- `.md` - Markdown
- `.py` - Python
- `.js`, `.ts`, `.jsx`, `.tsx` - JavaScript/TypeScript
- `.html`, `.css` - Web files
- `.json`, `.yaml`, `.yml`, `.toml`, `.xml` - Data files
- `.sh`, `.bat`, `.cmd`, `.ps1` - Shell scripts
- `.sql` - Database queries
- `.c`, `.cpp`, `.h`, `.hpp` - C/C++
- `.java`, `.cs`, `.go`, `.rb`, `.rs` - Other languages
- `.lua`, `.swift`, `.r` - Additional languages
- `.env`, `.ini`, `.cfg`, `.conf` - Config files
- `.log` - Log files
- `.csv` - Comma-separated values
- `.dockerfile` - Docker configuration

### Media & Documents

- **Images**: `.jpg`, `.jpeg`, `.png`, `.gif`, `.bmp`, `.webp`, `.ico`, `.svg`
  - Shows image thumbnail preview
- **Documents**: `.pdf`, `.docx`, `.xlsx`, `.pptx`
  - Shows document info
- **Media**: `.mp3`, `.mp4`, `.avi`, `.mov`, `.mkv`, `.flv`, `.wav`
  - Shows media info
- **Archives**: `.zip`, `.rar`, `.7z`, `.tar`, `.gz`, `.bz2`
  - Shows archive info

### Binary Files

- **Executables**: `.exe`, `.dll`, `.so`, `.dylib`, `.bin`
  - Shows executable info

## How to Use

### Step 1: Select a File

Navigate with arrow keys to highlight a file in the results list.

### Step 2: Press SPACE

Press the space bar to show the preview.

### Visual Hint

Look for the `SPACE PREVIEW` hint in the **top right corner** of the file card (similar to the TAB/Enter hint in the INSTALL action).

### Example

```
┌─────────────────────────────────────────────┬──────────────┐
│ FILE                                        │ SPACE PREVIEW │
├─────────────────────────────────────────────┴──────────────┤
│ requirements.txt                                            │
│ ~/OneDrive/Pulpit/om/requirements.txt                       │
├─────────────────────────────────────────────────────────────┤
│ PyQt6>=6.5.0                                                │
│ flask>=2.3.0                                                │
│ requests>=2.31.0                                            │
│ ...                                                          │
└─────────────────────────────────────────────────────────────┘
```

## Preview Content

### Text Files

Shows the first 5000 characters (up to ~5KB) with:

- First few lines of content
- Syntax preserved (no highlighting, but readable)
- Formatted text (markdown, code indentation, etc.)

### Image Files

Shows:

- Thumbnail preview (up to 250px height)
- File information (filename, type)

### Documents & Media

Shows:

- File information
- Type and format
- Cannot show full content (binary format)

### Config Files

Shows:

- Configuration values
- Settings and parameters
- Environment variables

## Keyboard Navigation

| Key             | Action                |
| --------------- | --------------------- |
| `Arrow Up/Down` | Navigate file list    |
| `SPACE`         | Preview selected file |
| `Enter`         | Open selected file    |
| `Escape`        | Close preview/search  |

## Tips

1. **Quick Preview Loop**: Use arrows to navigate → Space to preview → Enter to open
2. **Reading Long Files**: Scroll through preview content if it's long
3. **Image Preview**: Double-preview to see full resolution (if available)
4. **Config Files**: Quickly review settings before opening in editor

## Technical Details

### Preview Sizes

- **Text files**: First 5000 characters
- **Images**: Scaled to max 250px height
- **Other files**: Metadata/file type info

### File Type Detection

Determined by file extension (case-insensitive):

- Looks for extension in supported lists
- Falls back to generic binary info for unknown types

### Performance

- Preview loads in background thread
- Non-blocking - UI remains responsive
- Fast display (~100ms after space press)

### Caching

- Recent previews are cached in memory
- Improves performance for repeated previews
- Memory footprint: minimal (~1-5MB for typical files)

## Supported Extensions Reference

### Programming Languages

```
Python: .py
JavaScript: .js, .ts, .jsx, .tsx
Web: .html, .css
C/C++: .c, .cpp, .h, .hpp
Java: .java
C#: .cs
Go: .go
Rust: .rs
Ruby: .rb
Lua: .lua
Swift: .swift
R: .r
SQL: .sql
```

### Data & Config

```
JSON: .json
YAML: .yaml, .yml
TOML: .toml
XML: .xml
INI: .ini
ENV: .env
Config: .cfg, .conf
CSV: .csv
```

### Shell & Scripting

```
Bash: .sh
Batch: .bat
PowerShell: .ps1
Command: .cmd
Dockerfile: .dockerfile
```

### Documents & Media

```
Text: .txt, .md, .log
Documents: .pdf, .docx, .xlsx, .pptx
Images: .jpg, .jpeg, .png, .gif, .bmp, .webp, .ico, .svg
Audio: .mp3, .wav
Video: .mp4, .avi, .mov, .mkv, .flv
Archives: .zip, .rar, .7z, .tar, .gz, .bz2
```

## Limitations

- Binary files show metadata only (not raw content)
- Very large files (>5MB) preview first 5KB only
- Media files show info, not actual playback
- PDF content is not extracted (file info shown)

## Future Enhancements

1. **Syntax Highlighting** - Color-coded code in preview
2. **Search in Preview** - Ctrl+F to search within preview content
3. **Export Preview** - Copy preview content to clipboard
4. **Custom Preview Height** - User-configurable preview size
5. **File Comparison** - Side-by-side preview of similar files
6. **Media Playback** - Play audio/video directly in preview

## Troubleshooting

### Preview Not Showing

- Verify file has supported extension
- Check file permissions (readable)
- Try a different file first

### Preview Shows Wrong Content

- File encoding might not be UTF-8
- Try opening file directly in editor for full content

### Preview is Slow

- File might be very large
- First preview loads in background - wait a moment
- Subsequent previews are instant (cached)

### Special Characters Not Displaying

- Text encoding issue
- Some binary content in supposedly text file
- File may be corrupted

---

**Feature Status**: ✅ Complete and Ready to Use
