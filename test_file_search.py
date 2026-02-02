#!/usr/bin/env python3
"""
Test script for the file search feature.
Tests the FileMatcher and FileSearchWorker.
"""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.search.file_matcher import FileMatcher, quick_search


def test_file_matcher():
    """Test the FileMatcher with various queries."""
    print("=" * 60)
    print("Testing FileMatcher")
    print("=" * 60)
    
    matcher = FileMatcher(max_results=10, search_depth=3)
    
    # Test queries
    test_queries = [
        "python",
        "setup",
        "requirements",
        "readme",
        "config",
    ]
    
    for query in test_queries:
        print(f"\n[SEARCH] Searching for: '{query}'")
        print("-" * 60)
        
        matches = matcher.search_files(query)
        
        if not matches:
            print("  No matches found.")
        else:
            for i, match in enumerate(matches[:5], 1):
                file_type = "[FOLDER]" if match.is_dir else "[FILE]"
                print(f"  {i}. {file_type} | Score: {match.score:.2f}")
                print(f"     Path: {match.path}")
                print(f"     Name: {match.name}")
        
        print()


def test_quick_search():
    """Test the quick_search convenience function."""
    print("=" * 60)
    print("Testing quick_search() function")
    print("=" * 60)
    
    results = quick_search("config", max_results=5)
    
    print(f"\nFound {len(results)} results for 'config':")
    for result in results:
        print(f"  - {result['name']}: {result['score']:.2f}")


if __name__ == "__main__":
    try:
        test_file_matcher()
        test_quick_search()
        print("\n[SUCCESS] All tests completed successfully!")
    except Exception as e:
        print(f"\n[ERROR] Error during testing: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
