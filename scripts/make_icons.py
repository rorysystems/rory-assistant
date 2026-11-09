"""Gera os ícones do app a partir do vetor da marca Rory Systems (variação 01, fundo escuro).

Saídas em assets/:
  icon.ico       Windows (16..256)
  icon-256.png   janela no Linux/macOS
  icon-1024.png  base para .icns (macOS) e AppImage
  button.png     marca sem fundo, para o botão flutuante (desenha sobre círculo escuro)

Rodar:  .venv\\Scripts\\python.exe scripts\\make_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

BG = (13, 16, 23, 255)  # #0D1017
GRAD_A = (44, 74, 124)  # #2C4A7C (baixo-esquerda)
GRAD_B = (62, 214, 224)  # #3ED6E0 (cima-direita)
GREEN = (78, 220, 160, 255)  # #4EDCA0
SS = 4  # supersampling


def gradient(size: int) -> Image.Image:
    """Degradê diagonal de baixo-esquerda para cima-direita."""
    g = Image.new("RGBA", (size, size))
    px = g.load()
    for y in range(size):
        for x in range(size):
            t = (x + (size - 1 - y)) / (2 * (size - 1))
            px[x, y] = tuple(int(a + (b - a) * t) for a, b in zip(GRAD_A, GRAD_B)) + (255,)
    return g


def mark_layer(size: int, scale: float) -> Image.Image:
    """Marca centralizada numa camada transparente. scale = largura da marca / lado."""
    big = size * SS
    # caixa da marca no viewBox 64 (com metade do traço): x 8.75..59.25, y 14.75..53.25
    k = big * scale / 50.5
    ox = big / 2 - 34 * k
    oy = big / 2 - 34 * k

    def p(x: float, y: float) -> tuple[float, float]:
        return (ox + x * k, oy + y * k)

    stroke = 2.5 * k
    mask = Image.new("L", (big, big), 0)
    d = ImageDraw.Draw(mask)
    # linhas de ligação
    d.line([p(18, 44), p(34, 24), p(50, 44)], fill=255, width=round(stroke), joint="curve")
    for cx, cy in (p(18, 44), p(34, 24), p(50, 44)):
        d.ellipse([cx - stroke / 2, cy - stroke / 2, cx + stroke / 2, cy + stroke / 2], fill=255)
    # quadrados vazados: apaga o miolo (esconde as linhas) e desenha o contorno
    for x, y in ((10, 36), (42, 36)):
        outer = [*p(x - 1.25, y - 1.25), *p(x + 17.25, y + 17.25)]
        inner = [*p(x + 1.25, y + 1.25), *p(x + 14.75, y + 14.75)]
        d.rounded_rectangle(outer, radius=5.25 * k, fill=255)
        d.rounded_rectangle(inner, radius=2.75 * k, fill=0)
    # quadrado cheio
    d.rounded_rectangle([*p(26, 16), *p(42, 32)], radius=4 * k, fill=255)

    layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    layer.paste(gradient(big // SS).resize((big, big)), (0, 0), mask)
    dot = ImageDraw.Draw(layer)
    cx, cy = p(42, 18)
    r = 3 * k
    dot.ellipse([cx - r, cy - r, cx + r, cy + r], fill=GREEN)
    return layer.resize((size, size), Image.LANCZOS)


def app_icon(size: int) -> Image.Image:
    big = size * SS
    bg = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(bg).rounded_rectangle([0, 0, big - 1, big - 1], radius=big * 0.22, fill=BG)
    bg = bg.resize((size, size), Image.LANCZOS)
    # ícones pequenos: marca maior para continuar legível
    scale = 0.78 if size <= 32 else 0.70
    bg.alpha_composite(mark_layer(size, scale))
    return bg


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [app_icon(s) for s in sizes]
    frames[-1].save(ASSETS / "icon.ico", sizes=[(s, s) for s in sizes], append_images=frames[:-1])
    frames[-1].save(ASSETS / "icon-256.png")
    app_icon(1024).save(ASSETS / "icon-1024.png")
    mark_layer(256, 0.66).save(ASSETS / "button.png")
    print("ok:", ", ".join(p.name for p in sorted(ASSETS.iterdir())))


if __name__ == "__main__":
    main()
