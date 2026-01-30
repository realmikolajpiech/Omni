import sys
import os
import logging

# Add the current directory to sys.path so we can import src
sys.path.append(os.getcwd())

# Configure logging to see the output
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

from src.services.search.web_search import search_api

def test_search():
    query = "who is elon musk?"
    print(f"Testing search for: {query}")
    results = search_api(query)
    
    if results:
        print(f"Success! Found {len(results)} results.")
        for res in results[:3]:
            print(f"- {res.get('title')} ({res.get('url')})")
    else:
        print("Failed to get results.")

if __name__ == "__main__":
    test_search()
