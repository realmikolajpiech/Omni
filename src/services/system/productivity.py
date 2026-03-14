import subprocess
import logging
from datetime import datetime, timedelta

def _run_osascript(script: str, timeout: int = 30) -> str:
    """Run an AppleScript command and return stdout."""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if result.returncode != 0:
            logging.error(f"[productivity] AppleScript error: {result.stderr}")
            return f"Error: {result.stderr}"
        return result.stdout.strip()
    except Exception as e:
        logging.error(f"[productivity] Execution error: {e}")
        return f"Error: {str(e)}"

# -----------------------------------------------------------------------------
# Calendar
# -----------------------------------------------------------------------------

def get_calendar_events(days: int = 3) -> str:
    """Get calendar events for the next N days."""
    script = f'''
    set startDate to current date
    set endDate to startDate + ({days} * days)

    -- Launch Calendar in the background if it's not already running
    if application "Calendar" is not running then
        tell application "Calendar" to launch
        delay 1
    end if

    tell application "Calendar"
        set output to ""
        set allCalendars to calendars

        repeat with aCal in allCalendars
            set calName to name of aCal
            set relevantEvents to (every event of aCal whose start date is greater than or equal to startDate and start date is less than or equal to endDate)

            repeat with anEvent in relevantEvents
                set evtTitle to summary of anEvent
                set evtStart to start date of anEvent
                set evtEnd to end date of anEvent
                set evtDesc to description of anEvent
                if evtDesc is missing value then set evtDesc to ""

                set output to output & "Calendar: " & calName & "\\n"
                set output to output & "Event: " & evtTitle & "\\n"
                set output to output & "Start: " & (evtStart as string) & "\\n"
                set output to output & "End: " & (evtEnd as string) & "\\n"
                set output to output & "Description: " & evtDesc & "\\n"
                set output to output & "-----------------------------------\\n"
            end repeat
        end repeat
        return output
    end tell
    '''
    return _run_osascript(script) or "No upcoming events found."

def create_calendar_event(title: str, start_iso: str, duration_minutes: int = 60, description: str = "") -> str:
    """
    Create a calendar event.
    start_iso should be in 'YYYY-MM-DD HH:MM:SS' format.
    """
    try:
        # Validate ISO format roughly
        dt = datetime.fromisoformat(start_iso)
        # Construct components for AppleScript to avoid locale issues
        year = dt.year
        month = dt.month
        day = dt.day
        hour = dt.hour
        minute = dt.minute
    except ValueError:
        return "Error: start_time must be in 'YYYY-MM-DD HH:MM:SS' format."

    # Sanitize inputs to prevent AppleScript syntax errors
    title = title.replace('"', '\\"')
    description = description.replace('"', '\\"')
    
    script = f'''
    -- Launch Calendar in the background if it's not already running
    if application "Calendar" is not running then
        tell application "Calendar" to launch
        delay 1
    end if

    set eventDate to current date
    -- Set day to 1 first to avoid overflow if today is 31st and target month is Feb
    set day of eventDate to 1
    set year of eventDate to {year}
    set month of eventDate to {month}
    set day of eventDate to {day}
    set hours of eventDate to {hour}
    set minutes of eventDate to {minute}
    set seconds of eventDate to 0

    set endDate to eventDate + ({duration_minutes} * minutes)

    tell application "Calendar"
        if (exists calendar "Home") then
            set targetCal to calendar "Home"
        else
            set targetCal to first calendar
        end if
            
        tell targetCal
            make new event at end with properties {{summary:"{title}", start date:eventDate, end date:endDate, description:"{description}"}}
        end tell
    end tell
    return "Event created successfully."
    '''
    return _run_osascript(script)

# -----------------------------------------------------------------------------
# Mail
# -----------------------------------------------------------------------------

def get_unread_emails(limit: int = 5) -> str:
    """Get the N most recent unread emails (headers only — fetching body causes IMAP timeouts)."""
    # Launch Mail in the background if it's not already running
    launch_script = 'if application "Mail" is not running then tell application "Mail" to launch'
    _run_osascript(launch_script, timeout=5)

    script = f'''
    tell application "Mail"
        set output to ""
        -- 'every message whose' lets Mail filter natively without per-message AppleScript calls
        set unreadMessages to (every message of inbox whose read status is false)
        set msgCount to count of unreadMessages
        if msgCount is 0 then return "No unread emails."

        -- Take the most recent N (inbox is typically sorted oldest-first, so read from the end)
        set loopCount to {limit}
        if msgCount < loopCount then set loopCount to msgCount
        set startIdx to msgCount - loopCount + 1

        repeat with i from startIdx to msgCount
            set msg to item i of unreadMessages
            -- Only access cached header properties — avoids triggering full IMAP body download
            set msgSubject to subject of msg
            set msgSender to sender of msg
            set msgDate to date received of msg

            set output to output & "From: " & msgSender & "\\n"
            set output to output & "Subject: " & msgSubject & "\\n"
            set output to output & "Date: " & (msgDate as string) & "\\n"
            set output to output & "-----------------------------------\\n"
        end repeat
        return output
    end tell
    '''
    return _run_osascript(script)

def send_email(to_address: str, subject: str, body: str) -> str:
    """Send an email via macOS Mail app."""
    # Sanitize inputs for AppleScript
    to_address = to_address.replace('"', '\\"')
    subject = subject.replace('"', '\\"')
    body = body.replace('"', '\\"')

    script = f'''
    tell application "Mail"
        set newMessage to make new outgoing message with properties {{subject:"{subject}", content:"{body}", visible:false}}
        tell newMessage
            make new to recipient at end of to recipients with properties {{address:"{to_address}"}}
        end tell
        send newMessage
    end tell
    return "Email sent successfully."
    '''
    return _run_osascript(script)
