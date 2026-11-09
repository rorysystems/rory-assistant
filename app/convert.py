"""Transforma um anexo em blocos de conteúdo para mandar ao Claude.

- Imagem: bloco de imagem (redimensionada para no máx. 1568 px; o modelo reduz para
  isso de qualquer jeito, então maior só custa upload).
- PDF: decisão **por página** (portado do h3-sinfoniage-web): texto quando a página tem
  camada de texto confiável; escaneada segue o `image_mode` (OCR no PC e/ou imagem).
- Office/HTML/XML: MarkItDown -> markdown. Planilhas (.xlsx/.xls): CSV por aba. .csv: cru.
- Texto puro (código, .md, .json...): lido direto.
- Economia de tokens: acima de BIG_TEXT_CHARS (ou BIG_PDF_PAGES páginas) vai só o cabeçalho,
  uma prévia e o caminho do arquivo salvo ("use Read/Grep"). Cada mensagem tem um orçamento
  total (MESSAGE_CHARS) dividido entre os anexos.
"""

from __future__ import annotations

import base64
import io
import re
import string
from pathlib import Path
from typing import Any

MIN_CHARS_PER_PAGE = 40
MAX_JUNK_RATIO = 0.15
TARGET_LONG_EDGE = 1568
JPEG_QUALITIES = (85, 75, 65)
IMAGE_BUDGET = 3_500_000  # caracteres base64 por imagem (limite da API é 5 MB)
MAX_PDF_PAGES = 80
BIG_TEXT_CHARS = 20_000  # acima disto o anexo não vai inteiro
BIG_PDF_PAGES = 10
PREVIEW_CHARS = 3_000
MESSAGE_CHARS = 60_000  # orçamento de texto de todos os anexos de uma mensagem
IMAGE_COST_CHARS = 6_000  # ~1.500 tokens por imagem, em "caracteres" do orçamento
MAX_IMAGE_PAGES = 20  # páginas de PDF escaneado enviadas como imagem
PREVIEW_IMAGE_PAGES = 3  # no modo "grande", quantas páginas-imagem de prévia
OCR_LONG_EDGE = 2000

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MARKITDOWN_EXT = {".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".csv", ".html", ".htm", ".xml", ".epub", ".msg"}
TEXT_EXT = {
    ".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".log", ".sql",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".css", ".scss", ".ps1", ".bat", ".cmd", ".sh",
    ".c", ".h", ".cpp", ".cs", ".java", ".kt", ".go", ".rs", ".rb", ".php", ".lua", ".env.example",
}

_EXPECTED = set(string.printable) | set(
    "áàâãäéèêëíìîïóòôõöúùûüçñÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇÑºª°±µ§€–—’‘“”…≤≥³²·•×÷"
)


def kind_of(name: str) -> str:
    from .media import kind_for

    ext = Path(name).suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext == ".pdf":
        return "pdf"
    if ext in MARKITDOWN_EXT:
        return "office"
    return kind_for(name) or "text"


def looks_binary(content: bytes) -> bool:
    return b"\x00" in content[:8192]


def junk_ratio(text: str) -> float:
    if not text:
        return 1.0
    return sum(1 for c in text if c not in _EXPECTED) / len(text)


def usable_text(text: str | None) -> bool:
    clean = (text or "").strip()
    return len(clean) >= MIN_CHARS_PER_PAGE and junk_ratio(clean) <= MAX_JUNK_RATIO


def _normalize_spaces(text: str) -> str:
    text = text.replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def _encode(image: Any, fmt: str, quality: int) -> str:
    buffer = io.BytesIO()
    if fmt == "PNG":
        image.save(buffer, format="PNG", optimize=True)
    else:
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _fit(image: Any, prefer_png: bool) -> tuple[str, str]:
    """(base64, media_type) dentro do orçamento: PNG se couber (prints ficam nítidos),
    senão JPEG com qualidade decrescente, depois resolução."""
    if prefer_png:
        data = _encode(image, "PNG", 0)
        if len(data) <= IMAGE_BUDGET:
            return data, "image/png"
    current = image.convert("RGB")
    data = ""
    for _ in range(5):
        for quality in JPEG_QUALITIES:
            data = _encode(current, "JPEG", quality)
            if len(data) <= IMAGE_BUDGET:
                return data, "image/jpeg"
        w, h = current.size
        current = current.resize((int(w * 0.8), int(h * 0.8)))
    return data, "image/jpeg"


def image_block(content: bytes) -> dict[str, Any]:
    from PIL import Image

    with Image.open(io.BytesIO(content)) as original:
        original.load()
        image = original
        if getattr(original, "is_animated", False):
            original.seek(0)
        if image.mode not in ("RGB", "RGBA", "L"):
            image = image.convert("RGBA")
        image = image.copy()
    image.thumbnail((TARGET_LONG_EDGE, TARGET_LONG_EDGE))
    data, media_type = _fit(image, prefer_png=True)
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


# --- orçamento e texto grande ---------------------------------------------------------------
def new_budget() -> dict[str, int]:
    """Orçamento de uma mensagem (compartilhado entre os anexos dela)."""
    return {"chars": MESSAGE_CHARS}


def estimate_tokens(blocks: list[dict[str, Any]]) -> int:
    """Estimativa grosseira: ~4 caracteres por token; imagem ~1.500."""
    chars = 0
    for block in blocks:
        if block.get("type") == "text":
            chars += len(block.get("text") or "")
        elif block.get("type") == "image":
            chars += IMAGE_COST_CHARS
    return chars // 4


def _spend(budget: dict[str, int] | None, chars: int) -> None:
    if budget is not None:
        budget["chars"] = max(0, budget["chars"] - chars)


def _remaining(budget: dict[str, int] | None) -> int:
    return budget["chars"] if budget is not None else MESSAGE_CHARS


def _save_sidecar(path: str | None, suffix: str, text: str) -> str | None:
    """Grava o texto completo ao lado do anexo (na pasta de dados do app) para o Read/Grep."""
    if not path:
        return None
    try:
        target = path + suffix
        Path(target).write_text(text, encoding="utf-8")
        return target
    except OSError:
        return None


def _text_payload(
    label: str, text: str, path: str | None, budget: dict[str, int] | None,
    source: str | None = None, fence: bool = False, sidecar: str = ".txt",
) -> str:
    """Texto do anexo: inteiro se couber; senão cabeçalho + prévia + caminho do arquivo.
    `source`: arquivo que já contém o texto (ex.: o próprio .py); senão grava um sidecar."""

    def wrap(t: str) -> str:
        return f"```\n{t}\n```" if fence else t

    if len(text) <= BIG_TEXT_CHARS and len(text) <= _remaining(budget):
        _spend(budget, len(text))
        return f"{label}\n{wrap(text)}"
    full = source or _save_sidecar(path, sidecar, text)
    cut = min(PREVIEW_CHARS, max(500, _remaining(budget)))
    preview = text[:cut]
    _spend(budget, len(preview))
    note = (
        f"{label}\n[Grande demais para enviar inteiro: {len(text)} caracteres (~{len(text) // 4} tokens). "
        f"Abaixo só o começo ({len(preview)} caracteres)."
    )
    if full:
        note += f" Conteúdo completo em: {full} — use Read (com offset/limit) ou Grep para consultar só o que precisar."
    else:
        note += " O restante foi descartado."
    return f"{note}]\n{wrap(preview)}\n[... fim da prévia]"


# --- PDF ----------------------------------------------------------------------------------------
def _pdf_texts(content: bytes) -> tuple[list[str], int]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    total = len(reader.pages)
    texts: list[str] = []
    for page in reader.pages[:MAX_PDF_PAGES]:
        try:
            texts.append(_normalize_spaces(page.extract_text() or ""))
        except Exception:
            texts.append("")
    return texts, total


def _img_block(data: str, media_type: str) -> dict[str, Any]:
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


def _pdf_blocks(
    name: str, content: bytes, path: str | None = None, prefs: dict[str, Any] | None = None,
    budget: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    from . import media

    mode = (prefs or {}).get("image_mode", "image")  # image | ocr | both
    texts, total = _pdf_texts(content)
    count = len(texts)
    scanned = [i for i, t in enumerate(texts) if not usable_text(t)]
    page_text = {i: t for i, t in enumerate(texts) if i not in scanned}
    text_chars = sum(len(t) for t in page_text.values())
    want_ocr = mode in ("ocr", "both") and media.ocr_available()
    # já dá para saber que é grande sem renderizar nada
    big = count > BIG_PDF_PAGES or text_chars > BIG_TEXT_CHARS or text_chars > _remaining(budget)
    image_cap = PREVIEW_IMAGE_PAGES if big else MAX_IMAGE_PAGES

    images: dict[int, dict[str, Any]] = {}
    ocr_pages: set[int] = set()
    if scanned:
        import pypdfium2

        document = pypdfium2.PdfDocument(content)
        try:
            for index in scanned:
                if not want_ocr and len(images) >= image_cap:
                    continue
                page = document[index]
                width, height = page.get_size()
                ocr_text = ""
                if want_ocr:
                    pil = page.render(scale=OCR_LONG_EDGE / max(width, height, 1)).to_pil()
                    ocr_text = _normalize_spaces(media.ocr_image(pil))
                    if ocr_text:
                        page_text[index] = ocr_text
                        ocr_pages.add(index)
                    if (mode == "both" or not ocr_text) and len(images) < image_cap:
                        pil.thumbnail((TARGET_LONG_EDGE, TARGET_LONG_EDGE))
                        images[index] = _img_block(*_fit(pil, prefer_png=False))
                else:
                    pil = page.render(scale=TARGET_LONG_EDGE / max(width, height, 1)).to_pil()
                    images[index] = _img_block(*_fit(pil, prefer_png=False))
        finally:
            document.close()

    total_chars = sum(len(t) for t in page_text.values())
    big = big or total_chars > BIG_TEXT_CHARS or total_chars > _remaining(budget)

    header = f"[Anexo PDF: {name} — {total} página(s)"
    if total > MAX_PDF_PAGES:
        header += f"; só as primeiras {MAX_PDF_PAGES} foram lidas"
    if ocr_pages:
        header += f"; {len(ocr_pages)} página(s) escaneada(s) lida(s) por OCR no PC"
    header += "]"

    def page_label(i: int) -> str:
        return f"[{name} — página {i + 1}{' (OCR)' if i in ocr_pages else ''}]"

    blocks: list[dict[str, Any]] = [{"type": "text", "text": header}]
    if not big:
        for index in range(count):
            if index in images:
                blocks.append({"type": "text", "text": f"[{name} — página {index + 1} (imagem)]"})
                blocks.append(images[index])
                _spend(budget, IMAGE_COST_CHARS)
            if index in page_text:
                blocks.append({"type": "text", "text": f"{page_label(index)}\n{page_text[index]}"})
                _spend(budget, len(page_text[index]))
            elif index not in images:
                blocks.append({"type": "text", "text": f"[{name} — página {index + 1}: escaneada, não enviada (limite de {MAX_IMAGE_PAGES} imagens)]"})
        return blocks

    full_text = "\n\n".join(f"{page_label(i)}\n{page_text[i]}" for i in range(count) if i in page_text)
    sidecar = _save_sidecar(path, ".txt", full_text) if full_text else None
    cut = min(PREVIEW_CHARS, max(500, _remaining(budget)))
    preview = full_text[:cut]
    _spend(budget, len(preview))
    note = f"[PDF grande ({count} página(s), {total_chars} caracteres de texto, ~{total_chars // 4} tokens): não enviado inteiro."
    if sidecar:
        note += f" Texto completo extraído em: {sidecar} — use Read (offset/limit) ou Grep."
    if path:
        note += f' PDF original em: {path} — use Read com o parâmetro pages (ex.: "3-5") para ver páginas específicas.'
    note += "]"
    if preview:
        note += f"\nPrévia:\n{preview}\n[... fim da prévia]"
    blocks.append({"type": "text", "text": note})
    for index in sorted(images)[:PREVIEW_IMAGE_PAGES]:
        blocks.append({"type": "text", "text": f"[{name} — página {index + 1} (imagem, prévia)]"})
        blocks.append(images[index])
        _spend(budget, IMAGE_COST_CHARS)
    return blocks


# --- Office / planilhas / CSV ----------------------------------------------------------------------
_MD_NAN = re.compile(r"(?<=\|)\s*(?:NaN|nan|None)\s*(?=\|)")


def _clean_md(text: str) -> str:
    text = _MD_NAN.sub(" ", text)
    text = re.sub(r"(?m)^(\|[^\n]*)$", lambda m: re.sub(r" {2,}", " ", m.group(1)), text)
    return _normalize_spaces(text)


def _markitdown_text(name: str, content: bytes) -> str:
    from markitdown import MarkItDown, StreamInfo

    result = MarkItDown().convert_stream(
        io.BytesIO(content), stream_info=StreamInfo(extension=Path(name).suffix.lower(), filename=name)
    )
    return _clean_md(result.text_content or "")


def _sheets_csv(content: bytes) -> str:
    """Planilha -> CSV por aba (bem menos tokens que tabela Markdown), sem NaN e sem espaços extras."""
    import pandas as pd

    sheets = pd.read_excel(io.BytesIO(content), sheet_name=None, header=None, dtype=str)
    parts: list[str] = []
    for sheet, frame in sheets.items():
        frame = frame.dropna(how="all").dropna(axis=1, how="all").fillna("")
        if frame.empty:
            continue
        frame = frame.map(lambda v: re.sub(r"\s+", " ", str(v)).strip())
        body = frame.to_csv(index=False, header=False, lineterminator="\n")
        parts.append(f"## Aba: {sheet}\n{body.rstrip()}")
    return "\n\n".join(parts)


def _office_text(name: str, content: bytes) -> tuple[str, str]:
    """(texto, sufixo do sidecar)."""
    if Path(name).suffix.lower() in (".xlsx", ".xls"):
        try:
            return _sheets_csv(content), ".csv"
        except Exception:  # noqa: BLE001 - sem pandas/engine: cai no MarkItDown
            pass
    return _markitdown_text(name, content), ".md"


def _normalize_csv(text: str) -> str:
    """CSV cru, só sem quebras de linha do Windows e sem linhas vazias/só de separadores."""
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(ln for ln in lines if ln.strip(' ,;\t"')).strip()


def to_blocks(
    name: str, content: bytes, path: str | None = None, prefs: dict[str, Any] | None = None,
    budget: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Blocos de conteúdo da API para um anexo. Erro vira um bloco de texto explicando.
    `path`: o arquivo já salvo (áudio/vídeo precisam dele); `prefs`: preferências do app;
    `budget`: orçamento da mensagem (ver new_budget), dividido entre os anexos."""
    from . import media

    prefs = prefs or {}
    kind = kind_of(name)
    ext = Path(name).suffix.lower()
    try:
        if kind == "image":
            mode = prefs.get("image_mode", "image")  # image | ocr | both
            if mode == "image":
                _spend(budget, IMAGE_COST_CHARS)
                return [{"type": "text", "text": f"[Imagem anexada: {name}]"}, image_block(content)]
            text = media.ocr_bytes(content)
            note = f"[Imagem anexada: {name} — texto lido por OCR no PC]\n{text or '(nenhum texto legível na imagem)'}"
            _spend(budget, len(note))
            if mode == "both" or not text:
                _spend(budget, IMAGE_COST_CHARS)
                return [{"type": "text", "text": note}, image_block(content)]
            return [{"type": "text", "text": note}]
        if kind in ("audio", "video"):
            if not path:
                return [{"type": "text", "text": f"[Não consegui processar {name}: arquivo não salvo]"}]
            text = media.describe_media(path, name, kind, prefs)
            return [{"type": "text", "text": _text_payload(f"[{name}: texto extraído no PC]", text, path, budget)}]
        if kind == "pdf":
            return _pdf_blocks(name, content, path, prefs, budget)
        if ext == ".csv":
            text = _normalize_csv(content.decode("utf-8-sig", errors="replace"))
            return [{"type": "text", "text": _text_payload(f"[Anexo {name} (CSV)]", text, path, budget, source=path)}]
        if kind == "office":
            text, suffix = _office_text(name, content)
            label = f"[Anexo {name}, " + ("planilha em CSV por aba]" if suffix == ".csv" else "convertido para markdown]")
            return [{"type": "text", "text": _text_payload(label, text, path, budget, sidecar=suffix)}]
        if looks_binary(content):
            # binário desconhecido: decodificar como texto só gastaria tokens com lixo
            size = f"{len(content) / 1024 / 1024:.1f} MB" if len(content) > 1024 * 1024 else f"{len(content) // 1024} KB"
            return [{"type": "text", "text": f"[Arquivo anexado: {name} ({size}) — formato binário que o app não lê; não foi enviado o conteúdo]"}]
        text = content.decode("utf-8", errors="replace")
        return [{"type": "text", "text": _text_payload(f"[Arquivo anexado: {name}]", text, path, budget, source=path, fence=True)}]
    except Exception as error:  # noqa: BLE001 - anexo ruim não pode derrubar a mensagem
        return [{"type": "text", "text": f"[Não consegui ler o anexo {name}: {error}]"}]


def estimate(name: str, content: bytes) -> dict[str, Any]:
    """Estimativa de tokens ANTES de enviar (chip do anexo). Barata: não faz OCR nem transcreve.
    {'tokens': int | None, 'big': bool}; tokens None = não dá para estimar (áudio/vídeo)."""
    kind = kind_of(name)
    ext = Path(name).suffix.lower()
    try:
        if kind in ("audio", "video"):
            return {"tokens": None, "big": False}
        if kind == "image":
            from PIL import Image

            with Image.open(io.BytesIO(content)) as img:
                w, h = img.size
            scale = min(1.0, TARGET_LONG_EDGE / max(w, h, 1))
            return {"tokens": min(1600, max(50, int(w * scale * h * scale / 750))), "big": False}
        if kind == "pdf":
            texts, _ = _pdf_texts(content)
            usable = sum(len(t) for t in texts if usable_text(t))
            scanned = sum(1 for t in texts if not usable_text(t))
            tokens = usable // 4 + min(scanned, MAX_IMAGE_PAGES) * 1500
            return {"tokens": tokens, "big": len(texts) > BIG_PDF_PAGES or usable > BIG_TEXT_CHARS}
        if ext == ".csv":
            text = _normalize_csv(content.decode("utf-8-sig", errors="replace"))
        elif kind == "office":
            text = _office_text(name, content)[0]
        elif looks_binary(content):
            return {"tokens": 0, "big": False}
        else:
            text = content.decode("utf-8", errors="replace")
        return {"tokens": len(text) // 4, "big": len(text) > BIG_TEXT_CHARS}
    except Exception:  # noqa: BLE001
        return {"tokens": None, "big": False}


def to_markdown(path: str) -> str:
    """Converte um arquivo do disco para markdown (visualizador e botão 'Converter para .md').
    PDF: só o texto; páginas escaneadas não são renderizadas."""
    p = Path(path)
    content = p.read_bytes()
    if kind_of(p.name) == "pdf":
        texts, total = _pdf_texts(content)
        parts = [f"[{p.name} — {total} página(s)]"]
        for index, text in enumerate(texts):
            if usable_text(text):
                parts.append(f"[{p.name} — página {index + 1}]\n{text}")
            else:
                parts.append(f"[{p.name} — página {index + 1}: escaneada, sem texto]")
        return "\n\n".join(parts)
    return _markitdown_text(p.name, content)
