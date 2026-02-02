import logging
from PyQt6.QtCore import QThread, pyqtSignal
from src.services.search.file_matcher import FileMatcher


class FileSearchWorker(QThread):
    """Worker thread for performing file searches without blocking the UI - OPTIMIZED."""
    
    # Signal: emits (results_list, query) when search completes
    results_found = pyqtSignal(list, str)
    error_occurred = pyqtSignal(str)
    
    def __init__(self, query: str, max_results: int = 10):
        """
        Initialize the file search worker - FAST version.
        
        Args:
            query: Search query string
            max_results: Maximum number of results to return (default: 10 for speed)
        """
        super().__init__()
        self.query = query
        self.max_results = max_results
        # Reuse matcher across searches for caching benefits
        if not hasattr(FileSearchWorker, '_shared_matcher'):
            FileSearchWorker._shared_matcher = FileMatcher(max_results=max_results)
        self.matcher = FileSearchWorker._shared_matcher
        self.matcher.max_results = max_results  # Update max results
    
    def run(self):
        """Execute the search in the worker thread."""
        try:
            if not self.query or not self.query.strip():
                self.results_found.emit([], self.query)
                return
            
            # Perform the search
            logging.info(f"FileSearchWorker: Starting search for '{self.query}'")
            file_matches = self.matcher.search_files(self.query)
            
            # Convert to dictionaries with additional metadata
            results = []
            for match in file_matches:
                result = {
                    "path": match.path,
                    "name": match.name,
                    "is_dir": match.is_dir,
                    "score": match.score,
                    "type": "folder" if match.is_dir else "file"
                }
                results.append(result)
            
            logging.info(f"FileSearchWorker: Found {len(results)} matches for '{self.query}'")
            self.results_found.emit(results, self.query)
        
        except Exception as e:
            logging.error(f"FileSearchWorker error: {e}")
            self.error_occurred.emit(str(e))
