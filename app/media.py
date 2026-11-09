"""Áudio, vídeo e imagem processados no próprio PC: nada sai do computador e nada é cobrado.
Só o texto resultante vai para o Claude.

- Transcrição: faster-whisper (mesmo motor do V:\\projetos\\transcricao), CPU int8.
  O modelo é baixado do Hugging Face no 1º uso (~/.cache/huggingface) e fica em memória.
  PyAV (<17, por causa do faster-whisper 1.2.1) decodifica o áudio de qualquer
  contêiner, inclusive vídeo, sem ffmpeg.
- OCR: o OCR do próprio Windows (Windows.Media.Ocr, via pywinrt), em pt-BR quando
  houver o idioma instalado. Fora do Windows, devolve vazio.
- Vídeo: transcrição do áudio + OCR de alguns quadros espalhados pelo vídeo.
"""

from __future__ import annotations

import asyncio
import difflib
import io
import sys
import threading
import time
from pathlib import Path
from typing import Any

AUDIO_EXT = {".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".flac", ".wma", ".amr", ".weba"}
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".wmv", ".webm", ".m4v", ".3gp", ".flv", ".mpeg", ".mpg", ".ts"}
OCR_DUP_RATIO = 0.9  # quadros de vídeo com texto >= 90% igual são duplicados
WHISPER_MODELS = ("tiny", "base", "small", "medium", "large-v3", "turbo")

_model_lock = threading.Lock()
_model: Any = None
_model_name: str | None = None


def fmt_duration(seconds: float) -> str:
    seconds = int(round(seconds or 0))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# --- transcrição ------------------------------------------------------------------------
def _get_model(name: str) -> Any:
    global _model, _model_name
    from faster_whisper import WhisperModel

    if _model is None or _model_name != name:
        _model = WhisperModel(name, device="cpu", compute_type="int8")
        _model_name = name
    return _model


def has_audio(path: str) -> bool:
    import av

    try:
        with av.open(path) as container:
            return any(s.type == "audio" for s in container.streams)
    except Exception:
        return False


def transcribe(path: str, model: str = "small", language: str | None = "pt", beam_size: int = 5) -> dict[str, Any]:
    """{'text', 'language', 'duration', 'seconds'} — `language=None` detecta sozinho.
    beam_size=1 (ditado) é ~2x mais rápido; 5 para anexos de áudio/vídeo."""
    started = time.time()
    with _model_lock:  # um modelo, uma transcrição por vez (CPU)
        whisper = _get_model(model if model in WHISPER_MODELS else "small")
        segments, info = whisper.transcribe(path, language=language or None, vad_filter=True, beam_size=beam_size)
        parts = [seg.text.strip() for seg in segments]
    text = " ".join(p for p in parts if p).strip()
    return {
        "text": text,
        "language": info.language,
        "duration": info.duration,
        "seconds": round(time.time() - started, 1),
    }


# --- OCR do Windows ------------------------------------------------------------------------
def ocr_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        from winrt.windows.media.ocr import OcrEngine  # noqa: F401

        return True
    except Exception:
        return False


async def _ocr_async(image: Any) -> str:
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter

    engine = None
    for tag in ("pt-BR", "pt-PT", "en-US"):
        lang = Language(tag)
        if OcrEngine.is_language_supported(lang):
            engine = OcrEngine.try_create_from_language(lang)
            break
    engine = engine or OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        return ""

    image = image.convert("RGBA")
    limit = OcrEngine.max_image_dimension
    if max(image.size) > limit:
        image.thumbnail((limit, limit))
    # OCR do Windows quer BGRA8
    r, g, b, a = image.split()
    raw = Image_merge("RGBA", (b, g, r, a)).tobytes()
    writer = DataWriter()
    writer.write_bytes(raw)
    bitmap = SoftwareBitmap.create_copy_from_buffer(
        writer.detach_buffer(), BitmapPixelFormat.BGRA8, image.width, image.height, BitmapAlphaMode.PREMULTIPLIED
    )
    result = await engine.recognize_async(bitmap)
    return "\n".join(line.text for line in result.lines).strip()


def Image_merge(mode: str, bands: tuple[Any, ...]) -> Any:  # noqa: N802 - atalho do PIL
    from PIL import Image

    return Image.merge(mode, bands)


def ocr_image(image: Any) -> str:
    """Texto de uma imagem PIL (vazio se não houver OCR ou texto)."""
    if not ocr_available():
        return ""
    try:
        return asyncio.run(_ocr_async(image))
    except Exception:
        return ""


def ocr_bytes(content: bytes) -> str:
    from PIL import Image

    with Image.open(io.BytesIO(content)) as image:
        image.load()
        return ocr_image(image.copy())


# --- vídeo -------------------------------------------------------------------------------
def video_frames(path: str, count: int = 6) -> list[tuple[float, Any]]:
    """Alguns quadros espalhados pelo vídeo: [(segundo, imagem PIL)]."""
    import av

    frames: list[tuple[float, Any]] = []
    with av.open(path) as container:
        streams = [s for s in container.streams if s.type == "video"]
        if not streams:
            return frames
        stream = streams[0]
        duration = float(container.duration / av.time_base) if container.duration else 0.0
        if duration <= 0:
            return frames
        count = max(1, min(count, int(duration // 5) + 1))
        for i in range(count):
            target = duration * (i + 0.5) / count
            try:
                container.seek(int(target / stream.time_base), stream=stream, any_frame=False, backward=True)
                for frame in container.decode(stream):
                    frames.append((target, frame.to_image()))
                    break
            except Exception:
                continue
    return frames


def describe_media(path: str, name: str, kind: str, prefs: dict[str, Any]) -> str:
    """Texto que vai para o Claude no lugar do áudio/vídeo."""
    model = prefs.get("whisper_model") or "small"
    language = prefs.get("whisper_language") or None
    lines: list[str] = []
    transcript = None
    if has_audio(path):
        try:
            transcript = transcribe(path, model, None if language == "auto" else language)
        except Exception as error:  # noqa: BLE001
            lines.append(f"(não consegui transcrever o áudio: {error})")
    else:
        lines.append("(sem faixa de áudio)")

    label = "Vídeo" if kind == "video" else "Áudio"
    header = f"[{label} anexado: {name}"
    if transcript:
        header += f" — {fmt_duration(transcript['duration'])}, transcrito no PC em {transcript['seconds']}s"
    header += "]"

    if transcript is not None:
        lines.append("Transcrição:\n" + (transcript["text"] or "(nenhuma fala reconhecida)"))

    if kind == "video" and prefs.get("video_ocr", True):
        kept: list[str] = []
        texts = []
        for second, image in video_frames(path):
            text = ocr_image(image)
            key = " ".join(text.split()).lower()
            if not key or any(difflib.SequenceMatcher(None, key, old).quick_ratio() >= OCR_DUP_RATIO
                              and difflib.SequenceMatcher(None, key, old).ratio() >= OCR_DUP_RATIO for old in kept):
                continue
            kept.append(key)
            texts.append(f"[{fmt_duration(second)}] {text}")
        if texts:
            lines.append("Texto que aparece na tela (OCR de alguns quadros):\n" + "\n".join(texts))
    return header + "\n\n" + "\n\n".join(lines)


def kind_for(name: str) -> str | None:
    ext = Path(name).suffix.lower()
    if ext in AUDIO_EXT:
        return "audio"
    if ext in VIDEO_EXT:
        return "video"
    return None
