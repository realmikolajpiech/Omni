import os
import shutil
import logging
from datetime import datetime

# Common file extensions mapped to categories
FILE_CATEGORIES = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".tiff", ".ico", ".heic"},
    "Documents": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md", ".rtf", ".csv", ".odt", ".ods", ".odp"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso", ".dmg", ".pkg"},
    "Audio": {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".wma"},
    "Video": {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v"},
    "Executables": {".app", ".exe", ".msi", ".bat", ".sh", ".bin"},
    "Code": {".py", ".js", ".ts", ".html", ".css", ".java", ".cpp", ".c", ".h", ".json", ".xml", ".yaml", ".yml", ".sql", ".php", ".rb", ".go", ".rs", ".swift", ".kt"},
    "Books": {".epub", ".mobi", ".azw3"},
    "Fonts": {".ttf", ".otf", ".woff", ".woff2"}
}

# Mapping code extensions to language names for smarter sorting
CODE_LANGUAGES = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "CSS",
    ".java": "Java",
    ".cpp": "C++",
    ".c": "C",
    ".h": "C++",
    ".json": "JSON",
    ".xml": "XML",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".sql": "SQL",
    ".php": "PHP",
    ".rb": "Ruby",
    ".go": "Go",
    ".rs": "Rust",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell"
}

def organize_folder(path: str, strategy: str = "smart") -> str:
    """
    Organize files in a folder into subfolders based on a strategy.
    
    Args:
        path: The directory to organize.
        strategy: 
            - "smart": Groups by broad category (Images, Docs), but puts Code into language folders (Python, JS).
            - "type": Groups only by broad category (Images, Documents, Code, etc).
            - "date": Groups by Year/Month.
    
    Returns:
        A summary string of what was moved.
    """
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return f"Error: Path '{path}' does not exist."
    if not os.path.isdir(path):
        return f"Error: '{path}' is not a directory."

    # Stats
    moved_count = 0
    created_folders = set()
    errors = []

    # Iterate over files
    for entry in os.scandir(path):
        if entry.is_dir() or entry.name.startswith('.'):
            continue
        
        filename = entry.name
        file_path = entry.path
        _, ext = os.path.splitext(filename)
        ext = ext.lower()
        
        target_folder_name = "Other"
        
        if strategy == "date":
            # Date based organization
            mtime = os.path.getmtime(file_path)
            dt = datetime.fromtimestamp(mtime)
            target_folder_name = dt.strftime("%Y-%m")
            
        elif strategy in ["type", "smart"]:
            # Category based organization
            found_category = False
            for category, extensions in FILE_CATEGORIES.items():
                if ext in extensions:
                    target_folder_name = category
                    
                    # Smart mode: refine Code category
                    if strategy == "smart" and category == "Code":
                        lang = CODE_LANGUAGES.get(ext)
                        if lang:
                            target_folder_name = f"Code/{lang}"
                        else:
                            target_folder_name = "Code/Misc"
                    
                    found_category = True
                    break
            
            if not found_category:
                # If extension is unknown, maybe don't move it? Or put in "Other"?
                # User complaint: "added a lot of unnecessary folders".
                # Let's be conservative: if unknown, leave it alone OR put in "Misc".
                # Let's leave unknown files alone to avoid over-organizing.
                continue

        # Create target folder
        target_dir = os.path.join(path, target_folder_name)
        if not os.path.exists(target_dir):
            try:
                os.makedirs(target_dir)
                created_folders.add(target_folder_name)
            except OSError as e:
                errors.append(f"Failed to create {target_folder_name}: {e}")
                continue
        
        # Move file
        target_path = os.path.join(target_dir, filename)
        
        # Handle collision
        if os.path.exists(target_path):
            base, extension = os.path.splitext(filename)
            counter = 1
            while os.path.exists(target_path):
                new_filename = f"{base}_{counter}{extension}"
                target_path = os.path.join(target_dir, new_filename)
                counter += 1
        
        try:
            shutil.move(file_path, target_path)
            moved_count += 1
        except OSError as e:
            errors.append(f"Failed to move {filename}: {e}")

    # Cleanup empty folders if we created them but failed to move? 
    # (Unlikely with current logic, but good practice)
    
    summary = f"Organized {moved_count} files in '{path}' using strategy '{strategy}'."
    if created_folders:
        summary += f"\nCreated folders: {', '.join(sorted(created_folders))}"
    if errors:
        summary += f"\nErrors: {'; '.join(errors[:5])}"
        if len(errors) > 5:
            summary += f" and {len(errors) - 5} more."
            
    return summary
