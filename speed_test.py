#!/usr/bin/env python3
"""
Speed test for optimized file search.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.search.file_matcher import FileMatcher


def speed_test():
    """Test search speed with timing."""
    print("=" * 60)
    print("FILE SEARCH - SPEED TEST")
    print("=" * 60)
    
    matcher = FileMatcher(max_results=10, search_depth=3)
    
    test_queries = [
        "python",
        "setup",
        "requirements",
        "readme",
        "config",
        "test",
    ]
    
    total_time = 0
    
    for query in test_queries:
        start = time.time()
        results = matcher.search_files(query)
        elapsed = time.time() - start
        total_time += elapsed
        
        print(f"\nQuery: '{query}'")
        print(f"  Time: {elapsed*1000:.1f}ms")
        print(f"  Results: {len(results)}")
        if results:
            print(f"  Top: {results[0].name} (score: {results[0].score:.1f})")
    
    avg_time = total_time / len(test_queries)
    print(f"\n{'='*60}")
    print(f"Total time: {total_time*1000:.1f}ms")
    print(f"Average per query: {avg_time*1000:.1f}ms")
    print(f"Target: < 1000ms per query")
    
    if avg_time < 1.0:
        print("[PASS] Meets speed requirement! (< 1 second)")
    else:
        print("[FAIL] Too slow, needs optimization")
    
    print(f"{'='*60}")


if __name__ == "__main__":
    try:
        speed_test()
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
