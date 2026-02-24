"""Tool definitions and execution for function calling with the main LLM."""
import json
import logging

# ── Tool Schemas ─────────────────────────────────────────────────────────────

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "Search the web for current information, news, recent events, facts, "
                "prices, weather, sports scores, or anything that may have changed "
                "recently or isn't in the model's training knowledge."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query to look up on the web.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": (
                "Semantically search user's local files and documents stored on the "
                "computer. Use when asked about personal notes, journals, code files, "
                "PDFs, spreadsheets, or any content that might be saved locally."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search for in local files.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": (
                "Evaluate a mathematical expression precisely. Use for arithmetic, "
                "algebra, percentages, and any numerical computation where accuracy matters."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "The mathematical expression to evaluate, e.g. '(100 * 1.23) / 4 + 7'.",
                    }
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_images",
            "description": (
                "Search user's local image library by visual description or content. "
                "Use when asked about photos or images stored on the computer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Description of the image or photo to find.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_recall",
            "description": (
                "Retrieve facts, preferences and personal details about this user from long-term memory. "
                "Use proactively when the answer could depend on something the user has mentioned before — "
                "their job, habits, goals, relationships, preferences, or anything personal. "
                "Also use when the user asks 'do you remember…' or 'what do you know about…'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to look up in memory, e.g. 'diet preferences', 'job', 'workout routine'.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_save",
            "description": (
                "Permanently save a new fact or preference about the user to long-term memory. "
                "Use when the user shares something important about themselves that should be remembered "
                "across future conversations — preferences, life events, goals, relationships, habits."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "fact": {
                        "type": "string",
                        "description": "A concise, factual statement to remember, e.g. 'User is vegetarian' or 'User's girlfriend is called Ania'.",
                    }
                },
                "required": ["fact"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_terminal",
            "description": (
                "Execute a shell command on the user's macOS system and return its output. "
                "Use for any system task: reading battery/CPU/disk/RAM info, changing settings via "
                "defaults write or osascript, running scripts, managing files, getting uptime, etc. "
                "NEVER tell the user to open Terminal manually — just call this tool."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute, e.g. 'defaults write com.apple.dock autohide -bool true && killall Dock'.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Brief human-readable description of what this command does, e.g. 'Enable Dock autohide'.",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "install_app",
            "description": (
                "Install an application on the user's macOS using Homebrew. "
                "Use when the user asks to install, download, or get any app or CLI tool. "
                "Tries --cask first (GUI apps like firefox, vlc, discord), falls back to formula (CLI tools like git, ffmpeg)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The Homebrew package name, e.g. 'firefox', 'vlc', 'discord', 'git'.",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "uninstall_app",
            "description": (
                "Uninstall/remove an application from the user's macOS using Homebrew. "
                "Use when the user asks to uninstall, remove, or delete an app."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The Homebrew package or cask name to remove, e.g. 'firefox', 'vlc', 'discord'.",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_delete",
            "description": (
                "Delete or forget a specific memory about the user. "
                "Use when the user explicitly asks you to forget something, or when you learn that "
                "a previously stored fact is now outdated or incorrect."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Describe what to forget, e.g. 'my old job at Google' or 'that I was vegetarian'.",
                    }
                },
                "required": ["query"],
            },
        },
    },
]


# ── Tool execution ────────────────────────────────────────────────────────────

def execute_tool(name: str, arguments: dict) -> str:
    """Dispatch a tool call by name and return the result as a plain string."""
    try:
        if name == "search_web":
            return _tool_search_web(arguments.get("query", ""))
        elif name == "search_files":
            return _tool_search_files(arguments.get("query", ""))
        elif name == "calculate":
            return _tool_calculate(arguments.get("expression", ""))
        elif name == "search_images":
            return _tool_search_images(arguments.get("query", ""))
        elif name == "memory_recall":
            return _tool_memory_recall(arguments.get("query", ""))
        elif name == "memory_save":
            return _tool_memory_save(arguments.get("fact", ""))
        elif name == "memory_delete":
            return _tool_memory_delete(arguments.get("query", ""))
        elif name == "run_terminal":
            return _tool_run_terminal(arguments.get("command", ""), arguments.get("description", ""))
        elif name == "install_app":
            return _tool_install_app(arguments.get("name", ""))
        elif name == "uninstall_app":
            return _tool_uninstall_app(arguments.get("name", ""))
        else:
            return json.dumps({"error": f"Unknown tool: {name}"})
    except Exception as e:
        logging.error(f"[tools] Execution error in '{name}': {e}")
        return json.dumps({"error": str(e)})


def _tool_search_web(query: str) -> str:
    from src.services.search.web_search import perform_web_search
    if not query.strip():
        return "Error: empty query."
    logging.info(f"[tool:search_web] query={query!r}")
    result = perform_web_search(query)
    return result or "No web results found."


def _tool_search_files(query: str) -> str:
    from src.services.search.local_search import perform_file_search
    if not query.strip():
        return "Error: empty query."
    logging.info(f"[tool:search_files] query={query!r}")
    result = perform_file_search(query)
    return result or "No local files found matching that query."


def _tool_calculate(expression: str) -> str:
    from src.services.llm.chat import perform_calculation
    if not expression.strip():
        return "Error: empty expression."
    logging.info(f"[tool:calculate] expression={expression!r}")
    return perform_calculation(expression)


def _tool_search_images(query: str) -> str:
    from src.services.search.image_search import perform_image_search_with_fallback
    if not query.strip():
        return "Error: empty query."
    logging.info(f"[tool:search_images] query={query!r}")
    result = perform_image_search_with_fallback(query)
    return result or "No matching images found."


def _tool_memory_recall(query: str) -> str:
    from src.services.memory.memvid_store import get_user_memory
    if not query.strip():
        return "Error: empty query."
    logging.info(f"[tool:memory_recall] query={query!r}")
    result = get_user_memory(query)
    return result or "No memories found for that query."


def _tool_memory_save(fact: str) -> str:
    from src.services.memory.memvid_store import remember_fact
    fact = fact.strip()
    if not fact:
        return "Error: empty fact."
    logging.info(f"[tool:memory_save] fact={fact!r}")
    ok = remember_fact(fact)
    return f"Saved: {fact}" if ok else "Failed to save memory."


def _tool_memory_delete(query: str) -> str:
    from src.services.memory.memvid_store import delete_memory
    if not query.strip():
        return "Error: empty query."
    logging.info(f"[tool:memory_delete] query={query!r}")
    ok = delete_memory(query)
    return f"Deleted memories matching: {query}" if ok else "No matching memories found to delete."


def _tool_run_terminal(command: str, description: str = "") -> str:
    import subprocess
    command = command.strip()
    if not command:
        return "Error: empty command."
    logging.info(f"[tool:run_terminal] {description or command!r}")
    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=15
        )
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        output = stdout
        if stderr:
            output += ("\n" if output else "") + f"STDERR: {stderr}"
        if not output:
            output = f"Done (exit code {proc.returncode})"
        return output
    except subprocess.TimeoutExpired:
        return "Error: command timed out after 15 seconds."
    except Exception as e:
        return f"Error: {e}"


def _find_brew() -> str | None:
    import os
    for path in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew"):
        if os.path.exists(path):
            return path
    return None


def _tool_install_app(name: str) -> str:
    import subprocess, os
    name = name.strip().lower()
    if not name:
        return "Error: empty app name."
    brew = _find_brew()
    if not brew:
        return "Error: Homebrew not found. Install it from https://brew.sh"
    logging.info(f"[tool:install_app] name={name!r}")
    env = {**os.environ, "HOMEBREW_NO_AUTO_UPDATE": "1", "NONINTERACTIVE": "1"}
    try:
        result = subprocess.run(
            f"{brew} install --cask {name} || {brew} install {name}",
            shell=True, capture_output=True, text=True, timeout=300, env=env
        )
        output = (result.stdout + "\n" + result.stderr).strip()
        return output[:1200] if output else f"Installed {name}"
    except subprocess.TimeoutExpired:
        return "Error: install timed out after 5 minutes."
    except Exception as e:
        return f"Error: {e}"


def _tool_uninstall_app(name: str) -> str:
    import subprocess, os
    name = name.strip().lower()
    if not name:
        return "Error: empty app name."
    brew = _find_brew()
    if not brew:
        return "Error: Homebrew not found."
    logging.info(f"[tool:uninstall_app] name={name!r}")
    env = {**os.environ, "HOMEBREW_NO_AUTO_UPDATE": "1", "NONINTERACTIVE": "1"}
    try:
        result = subprocess.run(
            f"{brew} uninstall --cask {name} || {brew} uninstall {name}",
            shell=True, capture_output=True, text=True, timeout=120, env=env
        )
        output = (result.stdout + "\n" + result.stderr).strip()
        return output[:1200] if output else f"Uninstalled {name}"
    except subprocess.TimeoutExpired:
        return "Error: uninstall timed out."
    except Exception as e:
        return f"Error: {e}"
