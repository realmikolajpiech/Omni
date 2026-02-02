import os
import logging
from pathlib import Path
from typing import List, Tuple, Dict
import string

class FileMatch:
    """Represents a file/folder match with ranking score."""
    def __init__(self, path: str, name: str, is_dir: bool, score: float = 0.0):
        self.path = path
        self.name = name
        self.is_dir = is_dir
        self.score = score
    
    def __repr__(self):
        return f"FileMatch(path={self.path}, score={self.score:.2f})"
    
    def to_dict(self):
        return {
            "path": self.path,
            "name": self.name,
            "is_dir": self.is_dir,
            "score": self.score
        }


class FileMatcher:
    """Intelligent file/folder matcher with ranking algorithm - OPTIMIZED FOR SPEED."""
    
    def __init__(self, max_results: int = 15, search_depth: int = 3):
        """
        Initialize the FileMatcher.
        
        Args:
            max_results: Maximum number of results to return (optimized default: 15)
            search_depth: Maximum directory depth to search (optimized default: 3)
        """
        self.max_results = max_results
        self.search_depth = search_depth
        self.excluded_dirs = {
            '.git', '__pycache__', '.venv', 'venv', 'node_modules', 
            '.idea', '.vscode', '.pytest_cache', 'dist', 'build',
            '.egg-info', '*.egg-info', '.tox', '.mypy_cache',
            'env', '.env', 'site-packages', '$RECYCLE.BIN', 'System Volume Information',
            # Windows specific
            'AppData', 'Program Files', 'ProgramData', 'Windows', 'System32',
            'Users', 'Temp', 'tmp', 'cache', '$Recycle', 'hiberfil.sys',
            # macOS
            'Library', 'Applications', '.cache', '.config',
            # Linux
            'usr', 'var', 'etc', 'sys', 'proc', 'dev', 'boot', 'root'
        }
        self.result_cache = {}  # Cache recent searches
    
    def should_exclude_dir(self, dir_name: str) -> bool:
        """Check if directory should be excluded from search."""
        return dir_name in self.excluded_dirs or dir_name.startswith('.')
    
    def calculate_score(self, query: str, filename: str, full_path: str) -> float:
        """
        OPTIMIZED scoring - faster calculation without sacrificing quality.
        
        Scoring criteria:
        - Exact match: 1000
        - Starts with query: 500
        - Contains query: 100
        - Path proximity: penalty for depth
        """
        query_lower = query.lower()
        filename_lower = filename.lower()
        path_lower = full_path.lower()
        
        score = 0.0
        
        # 1. Exact filename match (FAST: direct comparison)
        if filename_lower == query_lower:
            return 1000.0
        
        # 2. Filename starts with query (FAST: single check)
        if filename_lower.startswith(query_lower):
            score = 500.0
            # Quick bonus: shorter = more specific
            score += max(0, 50 - len(filename) * 0.2)
            
            # Path depth penalty (FAST: count slashes)
            path_depth = full_path.count(os.sep)
            score -= min(path_depth * 3, 80)
            
            return score
        
        # 3. Query is contained in filename (FAST: in operator)
        if query_lower in filename_lower:
            score = 100.0
            
            # Path depth penalty
            path_depth = full_path.count(os.sep)
            score -= min(path_depth * 3, 60)
            
            return max(0, score)
        
        # 4. No good match = 0 (FAST: skip fuzzy matching entirely)
        return 0
    
    def _fuzzy_score(self, query: str, text: str) -> float:
        """
        Simple fuzzy matching score.
        Returns a value between 0 and 1.
        """
        query = query.lower()
        text = text.lower()
        
        if not query:
            return 0.0
        
        # Check if all characters from query appear in text in order
        query_idx = 0
        text_idx = 0
        matched = 0
        
        while query_idx < len(query) and text_idx < len(text):
            if query[query_idx] == text[text_idx]:
                matched += 1
                query_idx += 1
            text_idx += 1
        
        # Return ratio of matched characters to query length
        return matched / len(query) if query else 0.0
    
    def search_files(self, query: str, start_path: str = None) -> List[FileMatch]:
        """
        Search for files and folders matching the query - FAST VERSION.
        
        Uses intelligent search paths and early termination for speed.
        
        Args:
            query: Search query string
            start_path: Starting directory (defaults to smart search paths)
        
        Returns:
            List of FileMatch objects sorted by score (highest first)
        """
        if not query or not query.strip():
            return []
        
        # Check cache first
        cache_key = query.lower()
        if cache_key in self.result_cache:
            return self.result_cache[cache_key][:self.max_results]
        
        matches = []
        
        # FAST: Search in specific paths instead of whole home directory
        search_paths = self._get_smart_search_paths(start_path)
        
        try:
            for path in search_paths:
                if not os.path.exists(path):
                    continue
                
                self._recursive_search(query, path, matches, depth=0)
                
                # EARLY TERMINATION: If we have enough good results, stop searching
                if len(matches) >= self.max_results * 1.5:
                    break
        
        except Exception as e:
            logging.error(f"File search error: {e}")
        
        # Sort by score (descending) and cache
        matches.sort(key=lambda m: m.score, reverse=True)
        top_results = matches[:self.max_results]
        
        # Cache for quick repeat queries
        self.result_cache[cache_key] = top_results
        
        return top_results
    
    def _get_smart_search_paths(self, custom_path: str = None) -> List[str]:
        """
        Get intelligent search paths - prioritize fast access locations.
        Searches common project locations first, avoiding slow system directories.
        
        Returns:
            List of paths to search in priority order
        """
        paths = []
        
        # 1. Custom path if provided
        if custom_path and os.path.exists(custom_path):
            paths.append(custom_path)
            return paths
        
        # 2. Current working directory (fastest)
        cwd = os.getcwd()
        if os.path.exists(cwd):
            paths.append(cwd)
        
        # 3. Desktop (common location for projects)
        try:
            desktop = os.path.expanduser("~/Desktop")
            if os.path.exists(desktop):
                paths.append(desktop)
        except:
            pass
        
        # 4. Documents
        try:
            docs = os.path.expanduser("~/Documents")
            if os.path.exists(docs):
                paths.append(docs)
        except:
            pass
        
        # 5. Downloads
        try:
            downloads = os.path.expanduser("~/Downloads")
            if os.path.exists(downloads):
                paths.append(downloads)
        except:
            pass
        
        # 6. Projects folder if exists
        try:
            projects = os.path.expanduser("~/Projects")
            if os.path.exists(projects):
                paths.append(projects)
        except:
            pass
        
        # 7. Parent of current directory
        try:
            parent = os.path.dirname(cwd)
            if os.path.exists(parent) and parent != cwd:
                paths.append(parent)
        except:
            pass
        
        # Remove duplicates while preserving order
        seen = set()
        unique_paths = []
        for p in paths:
            p_abs = os.path.abspath(p)
            if p_abs not in seen:
                seen.add(p_abs)
                unique_paths.append(p)
        
        return unique_paths if unique_paths else [os.path.expanduser("~")]
    
    def _recursive_search(self, query: str, path: str, matches: List[FileMatch], depth: int = 0) -> None:
        """
        Optimized recursive search through directories - FAST VERSION.
        
        Uses early termination and aggressive filtering for speed.
        
        Args:
            query: Search query
            path: Current directory path
            matches: List to accumulate matches (will be modified in-place)
            depth: Current recursion depth
        """
        # FAST: Stop at shallower depth (3 instead of 5)
        if depth > self.search_depth:
            return
        
        # FAST: Stop if we have enough results
        if len(matches) >= self.max_results * 2:
            return
        
        if not os.path.isdir(path):
            return
        
        try:
            entries = os.listdir(path)
        except (PermissionError, OSError):
            return
        
        # FAST: Sort entries to check matches first
        entries_to_check = []
        for entry in entries:
            # FAST: Quick name-only pre-filter before full score calculation
            entry_lower = entry.lower()
            query_lower = query.lower()
            
            # Skip obvious non-matches immediately
            if query_lower not in entry_lower and not entry_lower.startswith(query_lower):
                continue
            
            entries_to_check.append(entry)
        
        for entry in entries_to_check:
            try:
                full_path = os.path.join(path, entry)
                is_dir = os.path.isdir(full_path)
                
                # Skip excluded directories early
                if is_dir and self.should_exclude_dir(entry):
                    continue
                
                # Calculate match score
                score = self.calculate_score(query, entry, full_path)
                
                # Only add if score is meaningful
                if score > 0:
                    match = FileMatch(full_path, entry, is_dir, score)
                    matches.append(match)
                
                # FAST: Recurse only for good matches or shallow depths
                if is_dir and depth < self.search_depth:
                    # Only recurse if directory name or has promising match
                    dir_score = self.calculate_score(query, entry, full_path)
                    if dir_score > 30 or depth < 2:  # Only recurse for good matches or very shallow
                        self._recursive_search(query, full_path, matches, depth + 1)
            
            except (PermissionError, OSError):
                continue
    
    def search_with_content(self, query: str, start_path: str = None, 
                           search_extensions: List[str] = None) -> List[Dict]:
        """
        Search files and also check file contents.
        
        Args:
            query: Search query
            start_path: Starting directory
            search_extensions: List of file extensions to search content (.txt, .md, .py, etc)
        
        Returns:
            List of matches with content preview
        """
        if search_extensions is None:
            search_extensions = ['.txt', '.md', '.py', '.js', '.html', '.json', '.csv']
        
        file_matches = self.search_files(query, start_path)
        results = []
        
        for match in file_matches:
            result = {
                "path": match.path,
                "name": match.name,
                "is_dir": match.is_dir,
                "score": match.score,
                "type": "folder" if match.is_dir else "file",
                "content_match": False,
                "content_preview": None
            }
            
            # If it's a file with matching extension, check content
            if not match.is_dir and any(match.path.lower().endswith(ext) for ext in search_extensions):
                try:
                    with open(match.path, 'r', errors='ignore') as f:
                        content = f.read(2000)  # Read first 2KB
                        if query.lower() in content.lower():
                            result["content_match"] = True
                            # Extract a preview snippet around the match
                            lines = content.split('\n')
                            for i, line in enumerate(lines[:10]):  # Check first 10 lines
                                if query.lower() in line.lower():
                                    result["content_preview"] = line.strip()[:100]
                                    break
                            # Boost score for content matches
                            result["score"] += 200
                except Exception as e:
                    logging.debug(f"Could not read file content {match.path}: {e}")
            
            results.append(result)
        
        # Re-sort by score if we added content matches
        results.sort(key=lambda x: x['score'], reverse=True)
        return results


def quick_search(query: str, max_results: int = 10) -> List[Dict]:
    """
    Quick file search convenience function.
    
    Args:
        query: Search query
        max_results: Maximum number of results
    
    Returns:
        List of file matches as dictionaries
    """
    matcher = FileMatcher(max_results=max_results)
    matches = matcher.search_files(query)
    return [m.to_dict() for m in matches]


def search_with_content(query: str, max_results: int = 10) -> List[Dict]:
    """
    Search with content matching convenience function.
    """
    matcher = FileMatcher(max_results=max_results)
    return matcher.search_with_content(query)
