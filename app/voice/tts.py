"""Text-to-speech chain: Sarvam AI (natural Indian voices, if a key is set) -> gTTS (free) -> None.
If both fail the browser falls back to its built-in speechSynthesis, so the answer is always spoken."""
import base64
import io
import re

import httpx

from app.config import settings

SARVAM_URL = "https://api.sarvam.ai/text-to-speech"
SARVAM_LANG = {"hi": "hi-IN", "mr": "mr-IN", "te": "te-IN", "ta": "ta-IN", "kn": "kn-IN", "bn": "bn-IN",
               "gu": "gu-IN", "pa": "pa-IN", "ml": "ml-IN", "en": "en-IN"}


def clean_for_speech(text: str) -> str:
    text = re.sub(r"\[[^\]]{1,40}\]", "", text)          # source tags are for the eye, not the ear
    text = re.sub(r"[*_#>`]", "", text)
    text = re.sub(r"^\s*[-•]\s*", "", text, flags=re.MULTILINE)
    return re.sub(r"\s+", " ", text).strip()


def synthesize(text: str, language: str) -> tuple[bytes, str, str] | None:
    """Returns (audio_bytes, mime_type, engine) or None."""
    text = clean_for_speech(text)[:1500]
    if not text:
        return None
    if settings.sarvam_api_key and language in SARVAM_LANG:
        try:
            r = httpx.post(SARVAM_URL, headers={"api-subscription-key": settings.sarvam_api_key},
                           json={"text": text[:1500], "target_language_code": SARVAM_LANG[language],
                                 "model": "bulbul:v2", "speaker": "anushka"}, timeout=40)
            r.raise_for_status()
            audios = r.json().get("audios") or []
            if audios:
                return base64.b64decode(audios[0]), "audio/wav", "Sarvam AI Bulbul"
        except Exception:
            pass
    try:
        from gtts import gTTS
        buf = io.BytesIO()
        gTTS(text=text, lang=language).write_to_fp(buf)
        return buf.getvalue(), "audio/mpeg", "gTTS"
    except Exception:
        return None
