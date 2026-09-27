from __future__ import annotations

import logging
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from desktop.captions import Captions
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
        self.captions = Captions(self)
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
            if sys.platform == "win32":
                self.captions.apply(
                    self.hint,
                    (
                        "The first person to message the bot in a private chat is allowed in. "
                        "Closing the window hides it; the bot keeps running."
                    ),
                    wrap=640,
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

        self.status_var = tk.StringVar(value="")
        self.status_label = ttk.Label(frame)
        self.status_label.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self._set_status("Stopped")

        self.token_var = tk.StringVar()
        self.key_var = tk.StringVar()
        self.repo_var = tk.StringVar()
        self.model_var = tk.StringVar(value="auto")
        self._show_token = tk.BooleanVar(value=False)
        self._show_key = tk.BooleanVar(value=False)
        self.autostart_var = tk.BooleanVar(value=False)
        self.launch_var = tk.BooleanVar(value=self.prefs.start_bot_on_launch)

        self.token_entry = self._secret_row(frame, 1, "Telegram token", self.token_var, self._show_token)
        self.key_entry = self._secret_row(frame, 2, "Cursor API key", self.key_var, self._show_key)

        repo_label = ttk.Label(frame)
        repo_label.grid(row=3, column=0, sticky="w", pady=4)
        self.captions.apply(repo_label, "Repository folder")
        ttk.Entry(frame, textvariable=self.repo_var).grid(row=3, column=1, sticky="ew", padx=(8, 8))
        browse = ttk.Button(frame, command=self._browse_repo)
        self.captions.apply(browse, "Browse…")
        browse.grid(row=3, column=2, sticky="ew")

        model_label = ttk.Label(frame)
        model_label.grid(row=4, column=0, sticky="w", pady=4)
        self.captions.apply(model_label, "Model")
        ttk.Entry(frame, textvariable=self.model_var).grid(row=4, column=1, sticky="ew", padx=(8, 8))

        checks = ttk.Frame(frame)
        checks.grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 4))
        autostart = ttk.Checkbutton(
            checks,
            variable=self.autostart_var,
            command=self._toggle_autostart,
        )
        self.captions.apply(autostart, "Start at login")
        autostart.grid(row=0, column=0, sticky="w")
        if not supports_login_autostart():
            autostart.state(["disabled"])
        else:
            try:
                self.autostart_var.set(login_autostart_enabled())
            except OSError:
                logger.exception("Could not read login autostart")
        launch = ttk.Checkbutton(
            checks,
            variable=self.launch_var,
            command=self._toggle_launch_pref,
        )
        self.captions.apply(launch, "Start the bot when the app opens")
        launch.grid(row=1, column=0, sticky="w", pady=(4, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(8, 8))
        save_btn = ttk.Button(buttons, command=self.save_settings)
        self.captions.apply(save_btn, "Save")
        save_btn.grid(row=0, column=0, padx=(0, 6))
        self.start_btn = ttk.Button(buttons, command=self.start_bot)
        self.captions.apply(self.start_btn, "Start")
        self.start_btn.grid(row=0, column=1, padx=(0, 6))
        self.stop_btn = ttk.Button(buttons, command=self.stop_bot, state="disabled")
        self.captions.apply(self.stop_btn, "Stop")
        self.stop_btn.grid(row=0, column=2, padx=(0, 6))
        log_btn = ttk.Button(buttons, command=self._open_log)
        self.captions.apply(log_btn, "Log")
        log_btn.grid(row=0, column=3, padx=(0, 6))
        quit_btn = ttk.Button(buttons, command=self.quit_app)
        self.captions.apply(quit_btn, "Quit")
        quit_btn.grid(row=0, column=4)

        hint = (
            "The first person to message the bot in a private chat is allowed in. "
            "Closing the window minimizes it. Quit stops the bot."
        )
        self.hint = ttk.Label(frame)
        self.captions.apply(self.hint, hint, wrap=640)
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
        name = ttk.Label(frame)
        self.captions.apply(name, label)
        name.grid(row=row, column=0, sticky="w", pady=4)
        entry = ttk.Entry(frame, textvariable=variable, show="*")
        entry.grid(row=row, column=1, sticky="ew", padx=(8, 8))

        def toggle() -> None:
            entry.configure(show="" if shown.get() else "*")

        reveal = ttk.Checkbutton(frame, variable=shown, command=toggle)
        self.captions.apply(reveal, "Show")
        reveal.grid(row=row, column=2, sticky="w")
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
        chosen = filedialog.askdirectory(initialdir=initial, title="Repository folder")
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
            messagebox.showerror("Cursor Telegram Bot", f"Could not save settings:\n{exc}", parent=self)
            return False
        self._set_status("Settings saved")
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
            messagebox.showerror("Cursor Telegram Bot", f"Could not start the bot:\n{exc}", parent=self)
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
        if sys.platform == "win32" and self.tray is not None and self.tray.alive:
            self.withdraw()
            return
        self.iconify()

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
            messagebox.showerror("Cursor Telegram Bot", f"Could not change login startup:\n{exc}", parent=self)

    def _toggle_launch_pref(self) -> None:
        self.prefs = UiPrefs(start_bot_on_launch=bool(self.launch_var.get()))
        try:
            save_prefs(self.prefs_path, self.prefs)
        except OSError as exc:
            messagebox.showerror("Cursor Telegram Bot", f"Could not save the setting:\n{exc}", parent=self)

    def _open_log(self) -> None:
        self.supervisor.log_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.supervisor.log_path.exists():
            self.supervisor.log_path.write_text("", encoding="utf-8")
        try:
            open_path(self.supervisor.log_path)
        except OSError as exc:
            messagebox.showerror("Cursor Telegram Bot", f"Could not open the log:\n{exc}", parent=self)

    def _set_status(self, text: str) -> None:
        if self.status_var.get() == text:
            return
        self.status_var.set(text)
        self.captions.apply(self.status_label, text)

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
            self._set_status(f"Running · pid {self.supervisor.pid}")
        elif not self.status_var.get().startswith("Settings"):
            self._set_status("Stopped")
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")
        if self.tray is not None and self.tray.alive and running != self._last_running:
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
                "The app is already running. Open it from the tray.",
            )
            root.destroy()
        return

    app = App(background=background, lock=lock)
    app.mainloop()
