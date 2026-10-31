"""Speech-to-text with Groq-hosted Whisper (supports Hindi, Marathi, Telugu, Tamil, Bengali, ... and
code-mixed speech). Falls back across API keys on rate limits."""
from groq import Groq, RateLimitError

from app.config import settings

WHISPER_LANG_TO_CODE = {
    "english": "en", "hindi": "hi", "marathi": "mr", "telugu": "te", "tamil": "ta", "kannada": "kn",
    "bengali": "bn", "gujarati": "gu", "punjabi": "pa", "malayalam": "ml", "urdu": "hi",
}


class STTError(Exception):
    pass


def transcribe(audio: bytes, filename: str, language: str | None = None) -> dict:
    if not audio or len(audio) < 1500:
        raise STTError("Recording too short — hold the mic button and speak for a few seconds.")
    if not settings.groq_keys:
        raise STTError("Speech recognition is not configured (GROQ_API_KEYS).")
    last = None
    for key in settings.groq_keys:
        client = Groq(api_key=key, max_retries=0, timeout=60)
        try:
            kwargs = dict(file=(filename, audio), model=settings.groq_stt_model, response_format="verbose_json",
                          temperature=0.0)
            if language and language != "auto":
                kwargs["language"] = language
            r = client.audio.transcriptions.create(**kwargs)
            text = (getattr(r, "text", "") or "").strip()
            if not text:
                raise STTError("Could not hear any words — please try again closer to the microphone.")
            detected = (getattr(r, "language", None) or language or "en").lower()
            return {"text": text, "language": WHISPER_LANG_TO_CODE.get(detected, detected[:2])}
        except RateLimitError as e:
            last = e
            continue
        except STTError:
            raise
        except Exception as e:
            raise STTError(f"Speech recognition failed: {type(e).__name__}")
    raise STTError(f"Speech recognition is rate-limited right now; please type the question. ({last})")
