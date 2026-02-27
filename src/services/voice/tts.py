import sys
import logging
import asyncio
import tempfile
import os
import subprocess
import threading

_stop_event = threading.Event()

# Map ISO 639-1 language codes to natural-sounding edge-tts voices
VOICE_MAP = {
    "pl": "pl-PL-ZofiaNeural",
    "en": "en-US-AriaNeural",
    "de": "de-DE-KatjaNeural",
    "fr": "fr-FR-DeniseNeural",
    "es": "es-ES-ElviraNeural",
    "it": "it-IT-ElsaNeural",
    "ru": "ru-RU-SvetlanaNeural",
    "ja": "ja-JP-NanamiNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    "ko": "ko-KR-SunHiNeural",
    "pt": "pt-BR-FranciscaNeural",
    "nl": "nl-NL-ColetteNeural",
    "tr": "tr-TR-EmelNeural",
    "ar": "ar-SA-ZariyahNeural",
    "hi": "hi-IN-SwaraNeural",
}

_POLISH_CHARS = frozenset("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")
_CJK_RANGES = [(0x4E00, 0x9FFF), (0x3040, 0x30FF), (0xAC00, 0xD7AF)]


def _detect_voice(text: str) -> str:
    """Detect language and return matching edge-tts voice."""
    from src.core.config import TTS_VOICE

    # Polish: definitive via unique characters
    if any(c in _POLISH_CHARS for c in text):
        return VOICE_MAP["pl"]

    # CJK: check unicode ranges
    for ch in text:
        cp = ord(ch)
        if any(lo <= cp <= hi for lo, hi in _CJK_RANGES):
            # rough split: hiragana/katakana → ja, hangul → ko, else zh
            if 0x3040 <= cp <= 0x30FF:
                return VOICE_MAP["ja"]
            if 0xAC00 <= cp <= 0xD7AF:
                return VOICE_MAP["ko"]
            return VOICE_MAP["zh"]

    # Use langdetect for everything else
    try:
        from langdetect import detect
        lang = detect(text)
        return VOICE_MAP.get(lang, TTS_VOICE)
    except Exception:
        return TTS_VOICE


def _get_stdin_player_cmd():
    """
    Return a command list for a player that reads MP3 from stdin, or None.
    Streaming to stdin means audio starts playing before generation is complete.
    """
    if sys.platform == "win32":
        return None  # Windows: temp-file fallback

    candidates = [
        # ffplay (ffmpeg suite) — most reliable MP3 stdin playback
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-i", "pipe:0"],
        # sox play
        ["play", "-q", "-t", "mp3", "-"],
        # mpv
        ["mpv", "--no-video", "--really-quiet", "-"],
        # mpg123
        ["mpg123", "-q", "-"],
    ]
    for cmd in candidates:
        result = subprocess.run(["which", cmd[0]], capture_output=True)
        if result.returncode == 0:
            return cmd
    return None


async def _run_tts(text: str, voice: str, stop_event: threading.Event):
    import edge_tts
    communicate = edge_tts.Communicate(text, voice=voice)

    stdin_cmd = _get_stdin_player_cmd()

    if stdin_cmd:
        # Streaming mode: audio starts playing as first chunks arrive from the API
        proc = subprocess.Popen(
            stdin_cmd,
            stdin=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        try:
            async for chunk in communicate.stream():
                if stop_event.is_set():
                    proc.terminate()
                    return
                if chunk["type"] == "audio":
                    try:
                        proc.stdin.write(chunk["data"])
                    except BrokenPipeError:
                        break
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass
        proc.wait()
    else:
        # Temp-file fallback (Windows or no suitable stdin player found)
        tmp = tempfile.mktemp(suffix=".mp3")
        await communicate.save(tmp)
        try:
            _play_file(tmp, stop_event)
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass


def _play_file(path: str, stop_event: threading.Event):
    """Play an audio file, polling stop_event to allow early termination."""
    import time

    if sys.platform == "darwin":
        proc = subprocess.Popen(["afplay", path], stderr=subprocess.DEVNULL)
    elif sys.platform == "win32":
        proc = subprocess.Popen(
            ["powershell", "-c",
             f'(New-Object System.Media.SoundPlayer "{path}").PlaySync()'],
            stderr=subprocess.DEVNULL,
        )
    else:
        player = _get_stdin_player_cmd()
        cmd = [player[0], path] if player else None
        if not cmd:
            logging.warning("No audio player found for temp-file playback.")
            return
        proc = subprocess.Popen(cmd, stderr=subprocess.DEVNULL)

    while proc.poll() is None:
        if stop_event.is_set():
            proc.terminate()
            break
        time.sleep(0.05)


def stop_playback():
    """Interrupt current speech immediately."""
    _stop_event.set()


def speak(text: str, voice: str = None):
    if not text or not text.strip():
        return

    _stop_event.clear()

    if voice is None:
        voice = _detect_voice(text)

    logging.info(f"TTS [{voice}]: {text[:60]}...")

    try:
        asyncio.run(_run_tts(text, voice, _stop_event))
    except Exception as e:
        logging.error(f"edge-tts error: {e}")
        _fallback_speak(text)


def _fallback_speak(text: str):
    if sys.platform == "darwin":
        subprocess.run(["say", "-r", "200", text], check=False)
    elif sys.platform == "win32":
        try:
            import win32com.client
            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            speaker.Rate = 1
            speaker.Speak(text)
        except Exception as e:
            logging.error(f"Win32 TTS fallback error: {e}")
    else:
        logging.warning(f"No TTS fallback available on platform: {sys.platform}")
