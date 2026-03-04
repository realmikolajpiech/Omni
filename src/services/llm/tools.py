"""Tool definitions and execution for function calling with the main LLM."""
import json
import logging
import re
import threading

# ── Per-request trust-request collector ──────────────────────────────────────
# Each Flask request runs in its own thread, so threading.local() keeps
# the pending list isolated per request.
_tls = threading.local()


def _get_pending() -> list:
    if not hasattr(_tls, "trust_requests"):
        _tls.trust_requests = []
    return _tls.trust_requests


def flush_pending_trust_requests() -> list:
    """Return and clear all trust_request actions accumulated this request."""
    reqs = list(_get_pending())
    _tls.trust_requests = []
    return reqs


# ── Temporary trust boost (set by UI on "Allow once" for request_permission) ─
# Raised to the required level for a single AI turn; cleared in on_ai_response.
_trust_boost: int = 0


def set_trust_boost(level: int) -> None:
    """Temporarily elevate effective trust level for the current AI turn."""
    global _trust_boost
    _trust_boost = level


def clear_trust_boost() -> None:
    """Clear the temporary trust boost after a request completes."""
    global _trust_boost
    _trust_boost = 0


def get_effective_trust() -> int:
    """Return the higher of the user's configured trust level and any active boost."""
    import src.core.settings_store as _ss
    return max(_ss.get("trust_level", 1), _trust_boost)

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
                "MUST be called IMMEDIATELY when the user shares a name, preference, correction, or personal detail. "
                "Do not just acknowledge — save it."
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
            "name": "get_calendar_events",
            "description": "Get upcoming calendar events from the macOS Calendar app.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Number of days to look ahead (default 3).",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_calendar_event",
            "description": "Create a new event in the macOS Calendar app.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title of the event."},
                    "start_iso": {"type": "string", "description": "Start time in 'YYYY-MM-DD HH:MM:SS' format."},
                    "duration_minutes": {"type": "integer", "description": "Duration in minutes (default 60)."},
                    "description": {"type": "string", "description": "Description or notes for the event."},
                },
                "required": ["title", "start_iso"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_unread_emails",
            "description": "Get recent unread emails from macOS Mail app.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max number of emails to retrieve (default 5).",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Draft and optionally send an email using macOS Mail app.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address."},
                    "subject": {"type": "string", "description": "Subject line."},
                    "body": {"type": "string", "description": "Email body content."},
                },
                "required": ["to", "subject", "body"],
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
    {
        "type": "function",
        "function": {
            "name": "organize_folder",
            "description": (
                "Organize files in a specific folder into subfolders based on file type or date. "
                "Use when the user asks to 'cleanup', 'organize', or 'tidy up' a folder. "
                "It intelligently groups code files by language (e.g. Python, JS) and media by type."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The directory path to organize, e.g. '~/Desktop/personal'.",
                    },
                    "strategy": {
                        "type": "string",
                        "enum": ["smart", "type", "date"],
                        "description": "Strategy: 'smart' (default, groups code by language), 'type' (broad categories), 'date' (YYYY-MM).",
                    }
                },
                "required": ["path"],
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
        elif name == "get_calendar_events":
            return _tool_get_calendar_events(arguments.get("days", 3))
        elif name == "create_calendar_event":
            return _tool_create_calendar_event(
                arguments.get("title", ""),
                arguments.get("start_iso", ""),
                arguments.get("duration_minutes", 60),
                arguments.get("description", "")
            )
        elif name == "get_unread_emails":
            return _tool_get_unread_emails(arguments.get("limit", 5))
        elif name == "send_email":
            return _tool_send_email(
                arguments.get("to", ""),
                arguments.get("subject", ""),
                arguments.get("body", "")
            )
        elif name == "organize_folder":
            return _tool_organize_folder(arguments.get("path", ""), arguments.get("strategy", "smart"))
        else:
            return json.dumps({"error": f"Unknown tool: {name}"})
    except Exception as e:
        logging.error(f"[tools] Execution error in '{name}': {e}")
        return json.dumps({"error": str(e)})


def _tool_search_web(query: str) -> str:
    from src.services.search.web_search import perform_web_search
    if not query.strip():
        return "Error: empty query."
    print(f"[TOOL:search_web] query={query!r}", flush=True)
    result = perform_web_search(query)
    print(f"[TOOL:search_web] result len={len(result)} preview={result[:120]!r}", flush=True)
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


# ── Terminal trust classification ─────────────────────────────────────────────

# Patterns that require trust level 3 (destructive / privileged)
_TERMINAL_L3 = [
    r'\brm\s',           # delete files
    r'\bsudo\b',         # privilege escalation
    r'\bchmod\b',        # permission change
    r'\bchown\b',        # ownership change
    r'\bdd\b',           # raw disk operations
    r'\bmkfs\b',         # format filesystem
    r'\bfdisk\b',        # disk partitioning
    r'diskutil\s+(erase|format|partition|zeroDisk|secureErase)',
    r'\bbrew\s+install\b',
    r'\bbrew\s+uninstall\b',
    r'\bnpm\s+install\b',
    r'\bpip\d?\s+install\b',
    r'\bapt(-get)?\s+install\b',
    r'\byum\s+install\b',
    r'\bdnf\s+install\b',
]

# Patterns that require trust level 2 (write to filesystem or system state)
_TERMINAL_L2 = [
    # ── File system writes ────────────────────────────────────────────────────
    r'\btouch\b',            # create / update file timestamp
    r'\bmkdir\b',            # create directory
    r'\bcp\b',               # copy file
    r'\bmv\b',               # move / rename file
    r'\btee\b',              # write to file while piping
    r'\bwget\b',             # download file to disk
    r'\bcurl\b.*\s-[a-z]*o', # curl saving output (-o / -O)
    r'(?<![=<>!])>{1,2}(?![>=])',  # shell redirect  >  or  >>
    # ── System state modifications ────────────────────────────────────────────
    r'\bdefaults\s+write\b',
    r'\bdefaults\s+delete\b',
    r'\bkillall\b',
    r'\bkill\s+(-\d+\s+)?\d+',
    r'\bosascript\b',
    r'\blaunchctl\b',
    r'\bpmset\b',
    r'\bnetworksetup\b',
    r'\bscutil\s+--set\b',
    r'\bsystemsetup\b',
]


def _terminal_required_trust_level(command: str) -> int:
    """Return the minimum trust level needed to run this terminal command.

    1 — read-only            (ioreg, df, system_profiler, sw_vers, cat, ls, …)
    2 — filesystem / system  (touch, mkdir, cp, mv, defaults write, killall, …)
    3 — destructive/privileged (rm, sudo, brew install, …)
    """
    lower = command.lower()
    for pat in _TERMINAL_L3:
        if re.search(pat, lower):
            return 3
    for pat in _TERMINAL_L2:
        if re.search(pat, lower):
            return 2
    return 1


def _tool_run_terminal(command: str, description: str = "") -> str:
    import subprocess

    command = command.strip()
    if not command:
        return "Error: empty command."

    required = _terminal_required_trust_level(command)
    current  = get_effective_trust()

    if current < required:
        # Queue a trust_request action so the UI can show a permission popup
        _get_pending().append({
            "type":           "trust_request",
            "required_level": required,
            "command":        command,
            "description":    description or command,
        })
        level_names = {1: "Assistant", 2: "Automation", 3: "Full Control"}
        return (
            f"[Permission required] '{level_names[required]}' trust is needed for this command. "
            f"The user is being prompted for one-time permission."
        )

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


def _tool_get_calendar_events(days: int) -> str:
    from src.services.system.productivity import get_calendar_events
    logging.info(f"[tool:get_calendar_events] days={days}")
    return get_calendar_events(days=int(days))


def _tool_create_calendar_event(title: str, start_iso: str, duration_minutes: int, description: str) -> str:
    from src.services.system.productivity import create_calendar_event
    title = title.strip()
    start_iso = start_iso.strip()
    if not title or not start_iso:
        return "Error: title and start_iso are required."
    logging.info(f"[tool:create_calendar_event] title={title!r} start={start_iso}")
    return create_calendar_event(title, start_iso, int(duration_minutes), description)


def _tool_get_unread_emails(limit: int) -> str:
    from src.services.system.productivity import get_unread_emails
    logging.info(f"[tool:get_unread_emails] limit={limit}")
    return get_unread_emails(limit=int(limit))


def _tool_send_email(to: str, subject: str, body: str) -> str:
    from src.services.system.productivity import send_email
    to = to.strip()
    subject = subject.strip()
    body = body.strip()
    if not to or not subject:
        return "Error: to and subject are required."
    logging.info(f"[tool:send_email] to={to!r} subject={subject!r}")
    return send_email(to, subject, body)



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

    # Trust level 3 required for installing software
    current = get_effective_trust()
    if current < 3:
        _get_pending().append({
            "type":           "trust_request",
            "required_level": 3,
            "command":        f"brew install {name}",
            "description":    f"Install {name} via Homebrew",
        })
        return "[Permission required] 'Full Control' trust is needed to install software."

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

    # Trust level 3 required for uninstalling software
    current = get_effective_trust()
    if current < 3:
        _get_pending().append({
            "type":           "trust_request",
            "required_level": 3,
            "command":        f"brew uninstall {name}",
            "description":    f"Uninstall {name} via Homebrew",
        })
        return "[Permission required] 'Full Control' trust is needed to uninstall software."

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

def _tool_organize_folder(path: str, strategy: str) -> str:
    from src.services.system.files import organize_folder
    path = path.strip()
    if not path:
        return "Error: empty path."

    # Trust level 2 required for organizing files
    current = get_effective_trust()
    if current < 2:
        _get_pending().append({
            "type":           "trust_request",
            "required_level": 2,
            "command":        f"organize {path}",
            "description":    f"Organize files in {path}",
        })
        return "[Permission required] 'Automation' trust is needed to organize files."

    logging.info(f"[tool:organize_folder] path={path!r} strategy={strategy!r}")
    return organize_folder(path, strategy)
