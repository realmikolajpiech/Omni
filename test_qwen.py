
import os
import sys
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)

# Add src to path
sys.path.append(os.getcwd())

from src.core.config import FAST_MODEL_PATH
from llama_cpp import Llama

def test_fast_model():
    if not os.path.exists(FAST_MODEL_PATH):
        print(f"Model not found at {FAST_MODEL_PATH}")
        return

    print(f"Loading model from {FAST_MODEL_PATH}...")
    try:
        llm = Llama(
            model_path=FAST_MODEL_PATH,
            n_ctx=8192,
            n_threads=4,
            verbose=False
        )
    except Exception as e:
        print(f"Failed to load model: {e}")
        return

    prompts = [
        ("what is 2+2", "NO"),
        ("phone number for pizza hut", "YES"),
        ("what is on my screen", "YES") # This is for screen intent
    ]

    sys_prompt_search = (
        "Decide if this query requires Google Search to answer correctly.\n"
        "Output ONLY 'YES' or 'NO'. Do NOT explain. Do NOT think.\n"
        "YES: Current events, news, specific people, places, weather, prices, sports, phone numbers, contact information, business hours, addresses, UNKNOWN terms, nonsense words, made-up words, slang, acronyms.\n"
        "NO: Greetings, math, coding, creative writing, philosophy.\n"
        "\n"
        "Examples:\n"
        "Query: phone number for X -> YES\n"
        "Query: numer telefonu do X -> YES\n"
        "Query: hello -> NO\n"
        "Query: what is 2+2 -> NO\n"
        "\n"
        "(If unsure, say YES)."
    )

    print("\nTesting Search Intent:")
    for query, expected in prompts[:2]:
        messages = [
            {"role": "system", "content": sys_prompt_search},
            {"role": "user", "content": f"Query: {query}"}
        ]
        output = llm.create_chat_completion(messages=messages, max_tokens=128, temperature=0.0)
        content = output['choices'][0]['message']['content'].strip()
        print(f"Query: '{query}' -> Output: '{content}'")

    sys_prompt_screen = (
        "Decide if this query requires SEEING the user's SCREEN (taking a screenshot) to answer.\n"
        "Output ONLY 'YES' or 'NO'.\n"
        "YES: 'what is on my screen?', 'summarize this page', 'who is in this video?', 'look at this code', 'explain this error', 'read this', 'which button should i click?', 'what do you see?'.\n"
        "NO: 'hello', 'hi', 'who are you?', 'how are you?', 'generate an image', 'find a photo of cats', 'what time is it'.\n"
        "\n"
        "Examples:\n"
        "Query: what is this website? -> YES\n"
        "Query: hello -> NO (greeting)\n"
        "Query: who are you? -> NO (identity question)\n"
        "Query: show me a cat -> NO (this is image generation/search)\n"
        "Query: look at this -> YES\n"
        "\n"
        "(If unsure, say NO)."
    )
    
    print("\nTesting Screen Intent:")
    query = "what is on my screen"
    messages = [
        {"role": "system", "content": sys_prompt_screen},
        {"role": "user", "content": f"Query: {query}"}
    ]
    output = llm.create_chat_completion(messages=messages, max_tokens=128, temperature=0.0)
    content = output['choices'][0]['message']['content'].strip()
    print(f"Query: '{query}' -> Output: '{content}'")

if __name__ == "__main__":
    test_fast_model()
