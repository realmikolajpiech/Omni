
import llama_cpp
import sys

print(f"llama-cpp-python version: {llama_cpp.__version__}")
try:
    import llama_cpp.llama_chat_format
    print("Available handlers in llama_chat_format:")
    for x in dir(llama_cpp.llama_chat_format):
        if "Handler" in x:
            print(f" - {x}")
except ImportError:
    print("Could not import llama_chat_format")
