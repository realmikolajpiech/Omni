# Plan: Add macOS Calendar and Mail Integration

I will add the ability for Omni to manage your macOS Calendar and Mail using native AppleScript integration. This avoids the need for external API keys and works directly with your local apps.

## 1. Create Productivity Service
**File:** `src/services/system/productivity.py`
This new service will handle all interactions with macOS Calendar and Mail apps via `osascript` (AppleScript).

**Capabilities:**
- **Calendar:**
    - `get_calendar_events(days=1)`: specific number of days to look ahead.
    - `create_calendar_event(title, start_time, duration_minutes, description)`: Create new events.
- **Mail:**
    - `get_unread_emails(limit=5)`: specific number of recent unread emails.
    - `send_email(to, subject, body)`: Draft and send emails (optionally showing the draft first).

## 2. Register LLM Tools
**File:** `src/services/llm/tools.py`
I will register 4 new tools so the AI can use them:
- `get_calendar_events`
- `create_calendar_event`
- `get_unread_emails`
- `send_email`

## 3. Implementation Details
- **Technology:** Python `subprocess` calling `osascript`.
- **Permissions:** The first time you use these features, macOS will ask for permission for "Terminal" (or the app running Omni) to control "Calendar" and "Mail". You will need to click "OK".
- **Safety:** Email sending will be implemented to create a visible draft window by default (or send directly if requested, but drafting is safer).

## Verification
- I will verify the code syntax and structure.
- You will need to verify the actual execution as it requires local macOS permissions.
