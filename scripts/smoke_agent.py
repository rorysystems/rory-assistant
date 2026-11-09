"""Teste rápido do backend sem janela: manda uma imagem com '42' e imprime os eventos.

Cria uma conversa temporária na pasta do usuário e apaga no fim (custa uma chamada).
"""

import io
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from app import config, convert  # noqa: E402
from app.agent import AgentHub  # noqa: E402
from app.store import Store  # noqa: E402

done = threading.Event()


def emit(event: dict) -> None:
    kind = event["type"]
    if kind == "text":
        print(event["text"], end="", flush=True)
    else:
        print(f"\n<{kind}> {({k: v for k, v in event.items() if k not in ('parts', 'conv')})}", flush=True)
    if kind == "done":
        done.set()


img = Image.new("RGB", (400, 200), "white")
draw = ImageDraw.Draw(img)
try:
    font = ImageFont.truetype("arial.ttf", 120)
except OSError:
    font = ImageFont.load_default()
draw.text((120, 30), "42", fill="red", font=font)
buf = io.BytesIO()
img.save(buf, "PNG")

store = Store()
previous_active = store.state.get("active")
conv = store.create(config.HOME)
hub = AgentHub(store, emit)
blocks = convert.to_blocks("print.png", buf.getvalue()) + [
    {"type": "text", "text": "Qual número aparece na imagem e de que cor? Responda em uma frase curta."}
]
hub.send(conv["id"], blocks, {"role": "user", "text": "teste", "attachments": [], "time": "2026-01-01T00:00:00"})
done.wait(180)
hub.shutdown()
store.delete(conv["id"])
store.state["active"] = previous_active
store.save()
