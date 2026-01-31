
try:
    import llama_cpp.llama_chat_format
    print("Available handlers:", dir(llama_cpp.llama_chat_format))
except ImportError:
    print("Could not import llama_chat_format")

try:
    from llama_cpp import Llama
    print("Llama init args:", Llama.__init__.__code__.co_varnames)
except:
    pass
