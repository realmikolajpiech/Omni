# Screenshot Hang Issue - Root Cause & Fix

## Problem Summary

The application was hanging for **3+ minutes** after logging `Requesting Screenshot from Client...` when the user asked questions that triggered screen intent detection (e.g., "hey", "hello", etc.).

### Example from logs:

```
2026-02-01 23:01:37,549 - INFO - Screen Intent: YES for 'hey'
2026-02-01 23:01:37,549 - INFO - Requesting Screenshot from Client...
[3 minutes of silence...]
2026-02-01 23:04:53,921 - INFO - Escape key pressed (Input Field)
```

## Root Cause Analysis

The hang occurred in a chain of events:

1. **Backend** (`chat.py` line 215-217): Detects screen intent is YES, returns `{"special_action": "screenshot_required"}`
2. **UI** (`window.py` line 1326-1330): Receives response, creates `ScreenshotWorker()` and calls `start()`
3. **ScreenshotWorker** (`screenshot_worker.py` line 19): Calls `QGuiApplication.primaryScreen().grabWindow(0)` **WITHOUT TIMEOUT**
4. **Windows Display Issue**: On Windows, `grabWindow(0)` can hang indefinitely if:
   - Display driver issues
   - Screen is being accessed exclusively by another application
   - GPU acceleration conflicts
   - System in screensaver/lock state

## Solution Implemented

### 1. **ScreenshotWorker Timeout** (screenshot_worker.py)

- Wrapped screenshot acquisition in a separate thread with 5-second timeout
- Added proper error handling for all screenshot methods
- Falls back gracefully if PyQt6 fails, tries Linux alternatives

```python
# Set a timeout for the entire screenshot operation (5 seconds)
screenshot_thread = threading.Thread(target=self._take_screenshot)
screenshot_thread.daemon = True
screenshot_thread.start()

# Wait for thread with timeout
screenshot_thread.join(timeout=5.0)

if screenshot_thread.is_alive():
    logging.error("Screenshot operation timed out after 5 seconds")
    self.failed.emit()
    return
```

### 2. **UI Timeout Handler** (window.py)

- Added QTimer that fires after 5 seconds if screenshot is still pending
- Automatically proceeds without screenshot if it takes too long
- Prevents user from being stuck in the UI

```python
# Add a timeout timer to handle stuck screenshot operations (5 seconds max)
self.screenshot_timeout_timer = QTimer()
self.screenshot_timeout_timer.setSingleShot(True)
self.screenshot_timeout_timer.setInterval(5000)  # 5 seconds
self.screenshot_timeout_timer.timeout.connect(lambda: self._handle_screenshot_timeout())
self.screenshot_timeout_timer.start()
```

### 3. **Intent Check Timeout** (chat.py)

- Added timeout parameter to model inference calls (10 seconds)
- Prevents the fast model from hanging on screen intent detection
- Defaults to NO if timeout occurs (safer fallback)

```python
out = model_manager.fast_model.create_chat_completion(
    messages=messages,
    max_tokens=256,
    timeout=10  # 10 second timeout for intent check
)
```

### 4. **Improved Logging**

- Added `[SCREENSHOT]` prefix to screenshot-related logs for easier debugging
- Logs timestamp of screenshot request and timeout info

```python
logging.info(f"[SCREENSHOT] Requesting Screenshot from Client for query: '{query}'")
logging.info("[SCREENSHOT] Client has 5 seconds to capture and return the screenshot")
```

## Expected Behavior After Fix

| Scenario                          | Before           | After                                             |
| --------------------------------- | ---------------- | ------------------------------------------------- |
| Screen query with working display | ✓ Works normally | ✓ Works normally (same)                           |
| Screen query with driver issue    | ❌ Hangs 3+ min  | ✅ Timeout after 5s, continues without screenshot |
| Intent check hangs                | ❌ Long pause    | ✅ Timeout after 10s, defaults to NO              |
| Multiple timeouts                 | ❌ User stuck    | ✅ User can press ESC or type new query           |

## Testing Recommendations

1. **Normal case**: Ask "what's on my screen?" → Should capture and analyze
2. **Display off**: Disconnect monitor or turn display off, ask same query → Should timeout after 5s and proceed
3. **Screensaver active**: Lock computer, ask query → Should timeout and proceed
4. **Keyboard interrupt**: During screenshot, press ESC → Should cancel gracefully

## Files Modified

1. **src/ui/workers/screenshot_worker.py**

   - Added threading wrapper with timeout
   - Added detailed error handling
   - Added timeout exception handling

2. **src/ui/window.py**

   - Added QTimer for screenshot timeout (5 seconds)
   - Added `_handle_screenshot_timeout()` method
   - Added logging for screenshot operations

3. **src/services/llm/chat.py**
   - Added timeout parameter to model calls
   - Improved logging with `[SCREENSHOT]` prefix
   - Added timeout exception handling with safe fallback

## Performance Impact

- **Positive**: No more hanging - max 5 second wait per screenshot request
- **Minimal overhead**: Threading is lightweight, only used during screenshot operations
- **Better UX**: User gets immediate feedback instead of mysterious 3-minute hang

## Future Improvements

1. Consider using `pyautogui.screenshot()` as alternative (simpler, more reliable on Windows)
2. Add UI progress indicator while screenshot is being captured
3. Add user preference to disable automatic screenshots entirely
4. Cache screenshot for X seconds to avoid repeated captures
5. Implement async screenshot queue to handle multiple requests
