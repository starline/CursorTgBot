"""Draw interface text when Tk's built-in font has no Cyrillic glyphs."""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path

_FONT_FILES = (
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    Path("/usr/share/fonts/truetype/freefont/FreeSans.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/mnt/c/Windows/Fonts/segoeui.ttf"),
    Path("C:/Windows/Fonts/segoeui.ttf"),
)


class Captions:
    def __init__(self, root: tk.Misc) -> None:
        self._root = root
        probe = tkfont.Font(root, family="TkDefaultFont", size=10)
        self.native = probe.measure("Я") > 0 and probe.measure("Сохранить") > 0
        self._path = next((path for path in _FONT_FILES if path.is_file()), None)
        self._cache: dict[tuple, tk.PhotoImage] = {}

    def apply(self, widget: tk.Widget, text: str, *, wrap: int | None = None) -> None:
        if self.native or self._path is None:
            widget.configure(text=text)
            return
        photo = self._render(text, wrap=wrap)
        options: dict = {"image": photo, "text": ""}
        if widget.winfo_class() == "TCheckbutton":
            options["compound"] = "left"
        widget.configure(**options)
        widget._caption_image = photo  # type: ignore[attr-defined]

    def _render(self, text: str, *, wrap: int | None) -> tk.PhotoImage:
        key = (text, wrap)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        from PIL import Image, ImageDraw, ImageFont, ImageTk

        font = ImageFont.truetype(str(self._path), 13)
        lines = _wrap_lines(text, font, wrap) if wrap else [text or " "]
        boxes = [font.getbbox(line) for line in lines]
        widths = [max(1, box[2] - box[0]) for box in boxes]
        heights = [max(1, box[3] - box[1]) for box in boxes]
        line_h = max(heights)
        gap = 4
        width = max(widths) + 2
        height = line_h * len(lines) + gap * (len(lines) - 1) + 2
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        y = 1
        for line, box in zip(lines, boxes):
            draw.text((1 - box[0], y - box[1]), line, font=font, fill=(25, 25, 25, 255))
            y += line_h + gap
        photo = ImageTk.PhotoImage(image, master=self._root)
        self._cache[key] = photo
        return photo


def _wrap_lines(text: str, font, width: int | None) -> list[str]:
    if not width or not text:
        return [text or " "]
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = word if not current else f"{current} {word}"
        if font.getlength(trial) <= width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [" "]
