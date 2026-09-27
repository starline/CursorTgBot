from __future__ import annotations

import logging
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from desktop.instance import AlreadyRunning, SingleInstance
from desktop.logtail import LogTail
from desktop.paths import project_root, runtime_dir
from desktop.platform import login_autostart_enabled, open_path, set_login_autostart, supports_login_autostart
from desktop.prefs import UiPrefs, load_prefs, save_prefs
from desktop.settings import BotForm, load_form, save_form, validate_form
from desktop.supervisor import BotSupervisor
from desktop.tray import TrayIcon

logger = logging.getLogger(__name__)


class App(tk.Tk):
    def __init__(self, *, background: bool, lock: SingleInstance) -> None:
        super().__init__()
        self._lock = lock
        self._quitting = False
        self._last_running = False
        self.prefs_path = runtime_dir() / "ui.json"
        self.prefs = load_prefs(self.prefs_path)
        self.supervisor = BotSupervisor()
        self.log_tail = LogTail(self.supervisor.log_path)
        self.tray: TrayIcon | None = None

        self.title("Cursor Telegram Bot")
        self.geometry("720x560")
        self.minsize(680, 460)
        self._build()
        self._load_into_form(load_form())
        self._apply_window_icon()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(200, self._tick)

        tray = TrayIcon(
            on_open=lambda: self.after(0, self._show),
            on_start=lambda: self.after(0, self.start_bot),
            on_stop=lambda: self.after(0, self.stop_bot),
            on_quit=lambda: self.after(0, self.quit_app),
            is_running=lambda: self.supervisor.running,
        )
        if tray.start():
            self.tray = tray
        else:
            self.hint.configure(
                text=(
                    "Первый, кто напишет боту в личку, получит доступ. "
                    "Закрытие окна останавливает бота и закрывает приложение."
                )
            )

        if background and self._can_stay_hidden():
            self.withdraw()
        if self.prefs.start_bot_on_launch and validate_form(self._form_from_widgets()) is None:
            self.after(200, self.start_bot)

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        frame = ttk.Frame(self, padding=12)
        frame.grid(sticky="nsew")
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(8, weight=1)

        self.status_var = tk.StringVar(value="Остановлен")
        ttk.Label(frame, textvariable=self.status_var).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        self.token_var = tk.StringVar()
        self.key_var = tk.StringVar()
        self.repo_var = tk.StringVar()
        self.model_var = tk.StringVar(value="auto")
        self._show_token = tk.BooleanVar(value=False)
        self._show_key = tk.BooleanVar(value=False)
        self.autostart_var = tk.BooleanVar(value=False)
        self.launch_var = tk.BooleanVar(value=self.prefs.start_bot_on_launch)

        self.token_entry = self._secret_row(frame, 1, "Токен Telegram", self.token_var, self._show_token)
        self.key_entry = self._secret_row(frame, 2, "Ключ Cursor API", self.key_var, self._show_key)

        ttk.Label(frame, text="Папка репозитория").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=self.repo_var).grid(row=3, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(frame, text="Обзор…", command=self._browse_repo).grid(row=3, column=2, sticky="ew")

        ttk.Label(frame, text="Модель").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=self.model_var).grid(row=4, column=1, sticky="ew", padx=(8, 8))

        checks = ttk.Frame(frame)
        checks.grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 4))
        autostart = ttk.Checkbutton(
            checks,
            text="Запускать при входе в систему",
            variable=self.autostart_var,
            command=self._toggle_autostart,
        )
        autostart.grid(row=0, column=0, sticky="w")
        if not supports_login_autostart():
            autostart.state(["disabled"])
        else:
            try:
                self.autostart_var.set(login_autostart_enabled())
            except OSError:
                logger.exception("Could not read login autostart")
        ttk.Checkbutton(
            checks,
            text="Запускать бота при открытии",
            variable=self.launch_var,
            command=self._toggle_launch_pref,
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(8, 8))
        ttk.Button(buttons, text="Сохранить", command=self.save_settings).grid(row=0, column=0, padx=(0, 6))
        self.start_btn = ttk.Button(buttons, text="Запустить", command=self.start_bot)
        self.start_btn.grid(row=0, column=1, padx=(0, 6))
        self.stop_btn = ttk.Button(buttons, text="Остановить", command=self.stop_bot, state="disabled")
        self.stop_btn.grid(row=0, column=2, padx=(0, 6))
        ttk.Button(buttons, text="Журнал", command=self._open_log).grid(row=0, column=3, padx=(0, 6))
        ttk.Button(buttons, text="Выход", command=self.quit_app).grid(row=0, column=4)

        hint = (
            "Первый, кто напишет боту в личку, получит доступ. "
            "Закрытие окна сворачивает его в трей, бот при этом продолжает работать."
        )
        self.hint = ttk.Label(frame, text=hint, wraplength=640)
        self.hint.grid(row=7, column=0, columnspan=3, sticky="w", pady=(0, 8))

        self.log = scrolledtext.ScrolledText(frame, height=12, wrap="word", state="disabled")
        self.log.grid(row=8, column=0, columnspan=3, sticky="nsew")

    def _secret_row(
        self,
        frame: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        shown: tk.BooleanVar,
    ) -> ttk.Entry:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
        entry = ttk.Entry(frame, textvariable=variable, show="*")
        entry.grid(row=row, column=1, sticky="ew", padx=(8, 8))

        def toggle() -> None:
            entry.configure(show="" if shown.get() else "*")

        ttk.Checkbutton(frame, text="Показать", variable=shown, command=toggle).grid(row=row, column=2, sticky="w")
        return entry

    def _load_into_form(self, form: BotForm) -> None:
        self.token_var.set(form.telegram_token)
        self.key_var.set(form.cursor_api_key)
        self.repo_var.set(form.repo_cwd)
        self.model_var.set(form.model)

    def _form_from_widgets(self) -> BotForm:
        return BotForm(
            telegram_token=self.token_var.get(),
            cursor_api_key=self.key_var.get(),
            repo_cwd=self.repo_var.get(),
            model=self.model_var.get(),
        )

    def _apply_window_icon(self) -> None:
        icon = project_root() / "packaging" / "windows" / "icon.ico"
        if sys.platform == "win32" and icon.exists():
            try:
                self.iconbitmap(str(icon))
            except tk.TclError:
                logger.info("Could not set window icon")

    def _browse_repo(self) -> None:
        current = self.repo_var.get().strip()
        initial = current if current and Path(current).is_dir() else str(Path.home())
        chosen = filedialog.askdirectory(initialdir=initial, title="Папка репозитория")
        if chosen:
            self.repo_var.set(chosen)

    def save_settings(self) -> bool:
        form = self._form_from_widgets()
        error = validate_form(form)
        if error:
            self._show()
            messagebox.showerror("Cursor Telegram Bot", error, parent=self)
            return False
        try:
            save_form(form)
        except OSError as exc:
            self._show()
            messagebox.showerror("Cursor Telegram Bot", f"Не удалось записать настройки:\n{exc}", parent=self)
            return False
        self.status_var.set("Настройки сохранены")
        return True

    def start_bot(self) -> None:
        if self.supervisor.running:
            return
        if not self.save_settings():
            self._show()
            return
        try:
            self.supervisor.start()
        except OSError as exc:
            messagebox.showerror("Cursor Telegram Bot", f"Не удалось запустить бота:\n{exc}", parent=self)
            self._show()

    def stop_bot(self) -> None:
        if not self.supervisor.running:
            return
        self.configure(cursor="watch")
        self.update_idletasks()
        try:
            self.supervisor.stop()
        finally:
            self.configure(cursor="")

    def quit_app(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        if self.supervisor.running:
            self.stop_bot()
        if self.tray is not None:
            self.tray.stop()
        self._lock.release()
        self.destroy()

    def _on_close(self) -> None:
        if self.tray is not None:
            self.withdraw()
            return
        self.quit_app()

    def _show(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()

    def _can_stay_hidden(self) -> bool:
        return self.tray is not None and validate_form(self._form_from_widgets()) is None

    def _toggle_autostart(self) -> None:
        enabled = bool(self.autostart_var.get())
        try:
            set_login_autostart(enabled)
        except OSError as exc:
            self.autostart_var.set(not enabled)
            messagebox.showerror("Cursor Telegram Bot", f"Не удалось изменить автозапуск:\n{exc}", parent=self)

    def _toggle_launch_pref(self) -> None:
        self.prefs = UiPrefs(start_bot_on_launch=bool(self.launch_var.get()))
        try:
            save_prefs(self.prefs_path, self.prefs)
        except OSError as exc:
            messagebox.showerror("Cursor Telegram Bot", f"Не удалось сохранить настройку:\n{exc}", parent=self)

    def _open_log(self) -> None:
        self.supervisor.log_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.supervisor.log_path.exists():
            self.supervisor.log_path.write_text("", encoding="utf-8")
        try:
            open_path(self.supervisor.log_path)
        except OSError as exc:
            messagebox.showerror("Cursor Telegram Bot", f"Не удалось открыть журнал:\n{exc}", parent=self)

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        line_count = int(self.log.index("end-1c").split(".")[0])
        if line_count > 400:
            self.log.delete("1.0", f"{line_count - 400}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _tick(self) -> None:
        if self._quitting:
            return
        self.supervisor.poll()
        chunk = self.log_tail.read_new()
        if chunk:
            self._append_log(chunk)
        running = self.supervisor.running
        if running:
            self.status_var.set(f"Запущен · pid {self.supervisor.pid}")
        elif not self.status_var.get().startswith("Настройки"):
            self.status_var.set("Остановлен")
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")
        if self.tray is not None and running != self._last_running:
            self.tray.set_running(running)
        self._last_running = running
        self.after(500, self._tick)


def run_app() -> None:
    background = "--background" in sys.argv[1:]
    try:
        lock = SingleInstance(runtime_dir() / "desktop.lock")
    except AlreadyRunning:
        if not background:
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo(
                "Cursor Telegram Bot",
                "Приложение уже запущено. Открой его из трея.",
            )
            root.destroy()
        return

    app = App(background=background, lock=lock)
    app.mainloop()
