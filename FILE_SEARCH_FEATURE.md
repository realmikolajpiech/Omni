# Real-Time File/Folder Search Feature

## Overview

This feature implements real-time file and folder search when users type in the search box. As you type, the system searches your disk for matching files and folders, displaying the most accurate matches first in the action list.

## Architecture

### Components

1. **FileMatcher** (`src/services/search/file_matcher.py`)

   - Core search engine with intelligent ranking algorithm
   - Searches files and folders on disk in real-time
   - Excludes common directories (`.git`, `node_modules`, `venv`, etc.)
   - Configurable search depth and result limits

2. **FileSearchWorker** (`src/ui/workers/file_search_worker.py`)

   - PyQt6 worker thread for async file searching
   - Prevents UI blocking while searching
   - Emits results back to main window when complete

3. **Window Integration** (`src/ui/window.py`)
   - Integrated into the main search flow
   - Triggered automatically with other searches
   - Results displayed using existing `FileActionWidget`

## Scoring Algorithm

The FileMatcher uses a sophisticated scoring system to rank results by relevance:

### Scoring Criteria (Best to Worst)

1. **Exact Match** (Score: 1000+)

   - Filename exactly matches query
   - `query: "readme"` → `README` (score: 1000)

2. **Prefix Match** (Score: 500+)

   - Filename starts with query
   - `query: "setup"` → `setup.py` (score: 546)
   - Shorter filenames get higher bonus

3. **Contains Match** (Score: 100+)

   - Query found anywhere in filename
   - Word boundary bonus if applicable
   - `query: "test"` → `my_test_file.txt` (score: 100+)

4. **Fuzzy Match** (Score: 50-99)

   - Characters from query appear in order in filename
   - `query: "cfg"` → `config.py` (score: ~25-50)

5. **Path Depth Penalty** (Score: -5 to -100)

   - Prefer shallower paths
   - Deeper nested files are penalized
   - Encourages finding files in accessible locations

6. **Path Match Bonus** (Score: +30)
   - Query appears in directory names
   - `query: "src"` → `/project/src/main.py` (bonus: +30)

### Examples

```
Query: "requirements"
- requirements.txt (exact match) → Score: 542
- sub/folder/requirements.md (prefix match in subfolder) → Score: 340

Query: "python"
- Python/ (exact folder match) → Score: 1005
- my_python_script.py (contains) → Score: 150

Query: "config"
- config/ (exact folder) → Score: 1000
- .config/ (excluded due to dot prefix)
- configuration.json (contains, no boundary) → Score: 120
```

## How It Works

### User Interaction Flow

```
1. User types in search box
   ↓
2. on_text_changed() triggered
   ↓
3. trigger_async_searches() called (after 650ms debounce)
   ↓
4. FileSearchWorker starts searching in background
   ↓
5. Results found, on_file_search_results() called
   ↓
6. refresh_list() updates display with file matches
   ↓
7. User sees matching files/folders in action list
```

### Real-Time Display

As you type, file/folder results are displayed:

- Below local app matches
- Above the "Ask Omni" option
- Each result shows:
  - File/folder icon and name
  - Full path
  - File preview (for text files)

### Opening Files/Folders

Click on any file/folder result to:

- **Files**: Open with default application
- **Folders**: Open in file explorer/Finder
- **Search continues** while file opens

## Configuration

### Search Depth

Default: 5 levels deep

- Controls how far into nested directories to search
- Adjust in `FileMatcher.__init__()`:

```python
matcher = FileMatcher(max_results=10, search_depth=5)
```

### Maximum Results

Default: 10 file/folder results per search

- Adjust `max_results` parameter
- Prevents UI clutter with too many results

### Excluded Directories

Default excluded: `.git`, `node_modules`, `venv`, `__pycache__`, etc.

Modify in `FileMatcher.excluded_dirs`:

```python
self.excluded_dirs = {
    '.git', '__pycache__', '.venv', 'venv', 'node_modules',
    # ... add more to exclude
}
```

## Performance

- **Search time**: ~0.5-2 seconds for typical queries
- **No blocking**: UI remains responsive during search
- **Debounced**: Waits 650ms after typing stops before searching
- **Depth limited**: Prevents exhaustive deep searches

## Supported File Types

The search handles all file types:

- Text files (`.txt`, `.md`, `.json`)
- Source code (`.py`, `.js`, `.html`)
- Folders/directories
- Any file on your system

### Content Search (Future)

Currently searches filenames and folder names. Content-based search can be added via:

```python
matcher.search_with_content(query, search_extensions=['.txt', '.md', '.py'])
```

## Troubleshooting

### Search is slow

- Reduce `search_depth` (currently 5)
- Add more directories to `excluded_dirs`
- Check if antivirus is scanning in real-time

### Not finding expected files

- Check if directory is in `excluded_dirs`
- Verify file/folder name contains query text
- Try a more specific query

### File won't open when clicked

- Ensure file still exists (may have been moved)
- Check file permissions
- Verify default application is configured

## Testing

Run the test script:

```bash
python test_file_search.py
```

This tests:

- FileMatcher scoring algorithm
- Search functionality
- Result ranking
- Quick search helper

## Technical Details

### Debouncing

Results are only fetched after 650ms of inactivity:

- Prevents excessive searching while typing
- Balances responsiveness with performance

### Thread Safety

- File search runs in separate PyQt6 worker thread
- Signals used for thread-safe communication
- Main UI thread never blocked

### Query Validation

- Empty queries ignored
- Whitespace trimmed
- Case-insensitive matching

## Future Enhancements

1. **Content Search**: Search file contents, not just names
2. **File Type Filters**: Search only certain file types
3. **Recent Files**: Priority boost for recently accessed files
4. **Favorites**: Bookmark frequently accessed files
5. **Search History**: Learn from user's search patterns
6. **Regex Support**: Advanced pattern matching
7. **Index Caching**: Cache filesystem index for faster searches
