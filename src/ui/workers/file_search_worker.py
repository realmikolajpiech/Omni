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
            
            # FALLBACK: If no results and query looks like a sentence, try content scan
            if not file_matches and len(self.query.split()) > 2:
                # 1. Try Semantic Search ONLY if LanceDB + embed_model are already warm.
                # Do NOT call ensure_main_model() here — it loads the heavy embedding model
                # (~3-4s) and blocks the UI on every keystroke. Skip straight to content
                # scan if the backend isn't already initialized.
                try:
                    import src.services.llm.model_manager as _mm
                    if _mm.db_conn is not None and _mm.embed_model is not None:
                        import os as _os
                        from src.services.search.local_search import search_lancedb
                        logging.info(f"FileSearchWorker: Trying semantic search for '{self.query}'")
                        semantic_results = search_lancedb(self.query, limit=5)
                        if semantic_results:
                            for res in semantic_results:
                                file_matches.append({
                                    "path": res['path'],
                                    "name": _os.path.basename(res['path']),
                                    "is_dir": _os.path.isdir(res['path']),
                                    "score": res['score'] * 1000,
                                    "type": "file",
                                    "content_preview": res['text'][:100]
                                })
                    else:
                        logging.debug(f"FileSearchWorker: Skipping semantic search (backend not ready)")
                except Exception as e:
                    logging.warning(f"FileSearchWorker: Semantic search failed/not ready: {e}")

                # 2. If still no results, use the lightweight Content Scan Fallback
                if not file_matches:
                    logging.info(f"FileSearchWorker: No semantic matches, trying content scan for '{self.query}'")
                    content_matches = self.matcher.search_content_scan(self.query)
                    if content_matches:
                        file_matches = content_matches
            
            # Convert to dictionaries with additional metadata (if not already dicts)
            results = []
            for match in file_matches:
                if isinstance(match, dict):
                    results.append(match)
                else:
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
