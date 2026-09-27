from __future__ import annotations

import logging
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)

RunningCheck = Callable[[], bool]
Action = Callable[[], None]


def tray_image(running: bool):
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    fill = (22, 163, 74, 255) if running else (15, 118, 110, 255)
    draw.rounded_rectangle((4, 4, 60, 60), radius=14, fill=fill)
    draw.rounded_rectangle((22, 18, 42, 40), radius=6, outline=(255, 255, 255, 255), width=3)
    draw.polygon([(28, 40), (28, 48), (38, 40)], fill=(255, 255, 255, 255))
    return image


class TrayIcon:
    def __init__(
        self,
        *,
        on_open: Action,
        on_start: Action,
        on_stop: Action,
        on_quit: Action,
        is_running: RunningCheck,
    ) -> None:
        self._on_open = on_open
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_quit = on_quit
        self._is_running = is_running
        self._icon = None

    def start(self) -> bool:
        try:
            import pystray
        except ImportError:
            logger.info("pystray/Pillow are not installed; tray icon is off")
            return False
        try:
            self._start_icon(pystray)
        except Exception:  # noqa: BLE001
            logger.exception("Tray icon failed")
            self._icon = None
            return False
        return True

    def _start_icon(self, pystray) -> None:
        menu = pystray.Menu(
            pystray.MenuItem("Открыть", lambda *_: self._on_open(), default=True),
            pystray.MenuItem("Запустить", lambda *_: self._on_start(), enabled=lambda *_: not self._is_running()),
            pystray.MenuItem("Остановить", lambda *_: self._on_stop(), enabled=lambda *_: self._is_running()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", lambda *_: self._on_quit()),
        )
        self._icon = pystray.Icon(
            "cursortgbot",
            tray_image(self._is_running()),
            "Cursor Telegram Bot",
            menu,
        )
        threading.Thread(target=self._icon.run, name="tray", daemon=True).start()

    def set_running(self, running: bool) -> None:
        if self._icon is None:
            return
        self._icon.icon = tray_image(running)
        self._icon.title = "Cursor Telegram Bot — запущен" if running else "Cursor Telegram Bot"

    def stop(self) -> None:
        if self._icon is None:
            return
        try:
            self._icon.stop()
        except Exception:  # noqa: BLE001
            logger.exception("Failed to stop tray icon")
        self._icon = None
