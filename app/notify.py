"""Aviso do sistema quando uma conversa termina ou pede permissão com a janela em segundo
plano. Windows: balão da bandeja (vira notificação do Windows, com som) + barra de
tarefas piscando; clicar no aviso traz a janela. macOS: osascript. Linux: notify-send."""

from __future__ import annotations

import subprocess
import sys
from typing import Any, Callable

from . import config
from .log import log


def _win(window: Any, title: str, text: str, on_click: Callable[[], None]) -> None:
    from System import Action  # type: ignore

    form = window.native

    def show() -> None:
        # thread da janela: WinForms só aqui
        import ctypes

        from System.Drawing import Icon  # type: ignore
        from System.Windows.Forms import NotifyIcon, ToolTipIcon  # type: ignore

        tray = getattr(window, "_rory_tray", None)
        if tray is None:
            tray = NotifyIcon()
            tray.Icon = Icon(str(config.ICON_ICO)) if config.ICON_ICO.exists() else form.Icon
            tray.Text = config.WINDOW_TITLE

            def clicked(_s: Any, _a: Any) -> None:
                tray.Visible = False
                cb = getattr(window, "_rory_notify_click", None)
                if cb:
                    cb()

            def closed(_s: Any, _a: Any) -> None:
                tray.Visible = False

            tray.BalloonTipClicked += clicked
            tray.BalloonTipClosed += closed
            tray.MouseClick += clicked
            window._rory_tray = tray
        window._rory_notify_click = on_click
        tray.Visible = True
        tray.ShowBalloonTip(8000, title, text, ToolTipIcon.Info)

        class FLASHWINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("hwnd", ctypes.c_void_p), ("dwFlags", ctypes.c_uint),
                        ("uCount", ctypes.c_uint), ("dwTimeout", ctypes.c_uint)]

        info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), int(form.Handle.ToInt64()), 3 | 12, 0, 0)  # FLASHW_ALL | TIMERNOFG
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))

    form.BeginInvoke(Action(show))


def focus(window: Any) -> None:
    """Traz a janela para a frente (clique no aviso)."""
    try:
        if sys.platform == "win32":
            from System import Action  # type: ignore
            from System.Windows.Forms import FormWindowState  # type: ignore

            form = window.native

            def front() -> None:
                # só desminimiza (restore() tiraria a janela do maximizado)
                if form.WindowState == FormWindowState.Minimized:
                    form.WindowState = FormWindowState.Normal
                form.Activate()
                form.BringToFront()

            form.BeginInvoke(Action(front))
        else:
            window.restore()
            window.show()
    except Exception as error:  # noqa: BLE001
        log.warning("aviso: não consegui trazer a janela: %s", error)


def show(window: Any, title: str, text: str, on_click: Callable[[], None]) -> bool:
    try:
        if sys.platform == "win32":
            _win(window, title, text, on_click)
        elif sys.platform == "darwin":
            esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
            script = f'display notification "{esc(text)}" with title "{esc(title)}" sound name "Glass"'
            subprocess.Popen(["osascript", "-e", script])
        else:
            subprocess.Popen(["notify-send", "-a", config.WINDOW_TITLE, title, text])
        return True
    except Exception as error:  # noqa: BLE001
        log.warning("aviso do sistema falhou: %s", error)
        return False


def dispose(window: Any) -> None:
    """Some com o ícone da bandeja ao fechar (senão fica um ícone fantasma)."""
    tray = getattr(window, "_rory_tray", None)
    if tray is not None:
        try:
            tray.Visible = False
            tray.Dispose()
        except Exception:  # noqa: BLE001
            pass
