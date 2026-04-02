"""Main application window for the Move Music Save Editor."""

from __future__ import annotations
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from editor.save_file import SaveFile
from gui.save_tab import SaveEditorTab
from gui.spawner import InterfaceSpawner


class MainWindow(tk.Tk):
    """Root application window."""

    APP_TITLE = "Move Music Save Editor"
    MIN_WIDTH = 780
    MIN_HEIGHT = 620

    def __init__(self) -> None:
        super().__init__()
        self.title(self.APP_TITLE)
        self.minsize(self.MIN_WIDTH, self.MIN_HEIGHT)
        self._save = SaveFile()
        self._spawner: Optional[InterfaceSpawner] = None
        self._unsaved = False
        self._build_menu()
        self._build_ui()
        self._update_title()

    # ------------------------------------------------------------------
    # Menu bar
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="New Save",        command=self._new_save,  accelerator="Ctrl+N")
        file_menu.add_command(label="Open Save…",      command=self._open_save, accelerator="Ctrl+O")
        file_menu.add_command(label="Save",            command=self._save_file, accelerator="Ctrl+S")
        file_menu.add_command(label="Save As…",        command=self._save_as,   accelerator="Ctrl+Shift+S")
        file_menu.add_separator()
        file_menu.add_command(label="Exit",            command=self._on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        spawn_menu = tk.Menu(menubar, tearoff=False)
        spawn_menu.add_command(label="Spawn ALL Interfaces",    command=self._spawn_all)
        spawn_menu.add_command(label="Close All Interfaces",    command=self._close_all_panels)
        spawn_menu.add_separator()
        for key, label in [
            ("main_menu", "Main Menu Panel"),
            ("song_list", "Song List Panel"),
            ("scoreboard", "Score HUD Panel"),
            ("left_ctrl", "Left Controller"),
            ("right_ctrl", "Right Controller"),
        ]:
            spawn_menu.add_command(
                label=label,
                command=lambda k=key: self._get_spawner().spawn(k)
            )
        menubar.add_cascade(label="Spawn", menu=spawn_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.config(menu=menubar)

        # Key bindings
        self.bind_all("<Control-n>", lambda _: self._new_save())
        self.bind_all("<Control-o>", lambda _: self._open_save())
        self.bind_all("<Control-s>", lambda _: self._save_file())
        self.bind_all("<Control-S>", lambda _: self._save_as())

    # ------------------------------------------------------------------
    # Main UI layout
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # ── Toolbar ───────────────────────────────────────────────────
        toolbar = ttk.Frame(self, padding=(6, 4))
        toolbar.pack(side="top", fill="x")

        ttk.Button(toolbar, text="📂 Open",     command=self._open_save).pack(side="left", padx=2)
        ttk.Button(toolbar, text="💾 Save",     command=self._save_file).pack(side="left", padx=2)
        ttk.Separator(toolbar, orient="vertical").pack(side="left", padx=6, fill="y")
        ttk.Button(toolbar, text="🎮 Spawn All Interfaces",
                   command=self._spawn_all).pack(side="left", padx=2)
        ttk.Button(toolbar, text="✖ Close Panels",
                   command=self._close_all_panels).pack(side="left", padx=2)

        # Spawn individual panel buttons
        ttk.Separator(toolbar, orient="vertical").pack(side="left", padx=6, fill="y")
        individual = [
            ("🏠 Menu",    "main_menu"),
            ("🎶 Songs",   "song_list"),
            ("🏆 Score",   "scoreboard"),
            ("◀ L-Ctrl",  "left_ctrl"),
            ("R-Ctrl ▶",  "right_ctrl"),
        ]
        for label, key in individual:
            ttk.Button(toolbar, text=label,
                       command=lambda k=key: self._get_spawner().spawn(k)
                       ).pack(side="left", padx=1)

        # File path label
        self._path_var = tk.StringVar(value="No file loaded  –  using default save data")
        ttk.Label(toolbar, textvariable=self._path_var, foreground="gray").pack(side="right", padx=8)

        # ── Status bar ────────────────────────────────────────────────
        self._status_var = tk.StringVar(value="Ready")
        status_bar = ttk.Label(self, textvariable=self._status_var, relief="sunken", anchor="w", padding=(6, 2))
        status_bar.pack(side="bottom", fill="x")

        # ── Notebook ──────────────────────────────────────────────────
        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill="both", expand=True, padx=8, pady=4)

        self._save_tab = SaveEditorTab(self._notebook, self._save, on_change=self._mark_unsaved)
        self._notebook.add(self._save_tab, text="  📝 Save Editor  ")

        # Spawner tab – a simple launch pad
        self._spawn_tab = self._build_spawn_tab()
        self._notebook.add(self._spawn_tab, text="  🎮 Interface Spawner  ")

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_spawn_tab(self) -> ttk.Frame:
        frame = ttk.Frame(self._notebook, padding=20)
        frame.columnconfigure((0, 1, 2), weight=1)

        header_lbl = ttk.Label(frame,
                               text="🎮  Spawn Move Music VR Interfaces on Your Desktop",
                               font=("Helvetica", 15, "bold"))
        header_lbl.grid(row=0, column=0, columnspan=3, pady=(0, 6))

        sub_lbl = ttk.Label(frame,
                             text="Open these panels before putting on your headset "
                                  "so you can preview and navigate the in-game UI on screen.",
                             wraplength=600)
        sub_lbl.grid(row=1, column=0, columnspan=3, pady=(0, 20))

        panels = [
            ("🏠  Main Menu",         "main_menu",  "Player card, nav buttons, XP bar"),
            ("🎶  Song List",          "song_list",  "Browse all songs with scores"),
            ("🏆  Score HUD",          "scoreboard", "Live score, combo & accuracy ring"),
            ("◀  Left Controller",    "left_ctrl",  "Animated left-hand controller"),
            ("▶  Right Controller",   "right_ctrl", "Animated right-hand controller"),
        ]

        for i, (label, key, desc) in enumerate(panels):
            row = (i // 3) + 2
            col = i % 3
            card = ttk.LabelFrame(frame, text=label, padding=12)
            card.grid(row=row, column=col, padx=8, pady=8, sticky="nsew")
            ttk.Label(card, text=desc, wraplength=160, justify="center").pack(pady=(0, 8))
            ttk.Button(card, text="Spawn",
                       command=lambda k=key: self._get_spawner().spawn(k)).pack(fill="x")

        for r in range(2, 5):
            frame.rowconfigure(r, weight=1)

        # Big "spawn all" button
        ttk.Button(frame, text="🚀  Spawn ALL Interfaces",
                   command=self._spawn_all,
                   style="Accent.TButton").grid(
            row=5, column=0, columnspan=3, pady=16, ipadx=20, ipady=8
        )
        ttk.Button(frame, text="✖  Close All Panels",
                   command=self._close_all_panels).grid(
            row=6, column=0, columnspan=3
        )
        return frame

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def _new_save(self) -> None:
        if not self._confirm_discard():
            return
        self._save = SaveFile()
        self._spawner = None
        self._reload_tab()
        self._unsaved = False
        self._path_var.set("No file loaded  –  using default save data")
        self._update_title()
        self._status("New default save created.")

    def _open_save(self) -> None:
        if not self._confirm_discard():
            return
        path = filedialog.askopenfilename(
            title="Open Move Music Save File",
            filetypes=[("JSON save files", "*.json"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            self._save.load(path)
        except Exception as exc:
            messagebox.showerror("Load Error", f"Could not load save file:\n{exc}")
            return
        self._spawner = None
        self._reload_tab()
        self._unsaved = False
        self._path_var.set(path)
        self._update_title()
        self._status(f"Loaded: {os.path.basename(path)}")

    def _save_file(self) -> None:
        if not self._save.path:
            self._save_as()
            return
        try:
            self._save.save()
        except Exception as exc:
            messagebox.showerror("Save Error", str(exc))
            return
        self._unsaved = False
        self._update_title()
        self._status("Save file written.")

    def _save_as(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save Move Music Save File As…",
            defaultextension=".json",
            filetypes=[("JSON save files", "*.json"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            self._save.save(path)
        except Exception as exc:
            messagebox.showerror("Save Error", str(exc))
            return
        self._unsaved = False
        self._path_var.set(path)
        self._update_title()
        self._status(f"Saved as: {os.path.basename(path)}")

    # ------------------------------------------------------------------
    # Spawner helpers
    # ------------------------------------------------------------------

    def _get_spawner(self) -> InterfaceSpawner:
        if self._spawner is None:
            self._spawner = InterfaceSpawner(self, self._save)
        return self._spawner

    def _spawn_all(self) -> None:
        self._get_spawner().spawn_all()
        self._status("All interface panels spawned.")

    def _close_all_panels(self) -> None:
        if self._spawner:
            self._spawner.close_all()
        self._status("All panels closed.")

    # ------------------------------------------------------------------
    # Misc helpers
    # ------------------------------------------------------------------

    def _reload_tab(self) -> None:
        self._save_tab._save = self._save
        self._save_tab.reload()

    def _mark_unsaved(self) -> None:
        self._unsaved = True
        self._update_title()

    def _update_title(self) -> None:
        dirty = " *" if self._unsaved else ""
        fname = os.path.basename(self._save.path) if self._save.path else "Untitled"
        self.title(f"{self.APP_TITLE}  –  {fname}{dirty}")

    def _status(self, msg: str) -> None:
        self._status_var.set(msg)

    def _confirm_discard(self) -> bool:
        if not self._unsaved:
            return True
        return messagebox.askyesno(
            "Unsaved Changes",
            "You have unsaved changes. Discard them and continue?"
        )

    def _on_close(self) -> None:
        if not self._confirm_discard():
            return
        self._close_all_panels()
        self.destroy()

    def _show_about(self) -> None:
        messagebox.showinfo(
            "About",
            "Move Music Save Editor\n\n"
            "A vibe-coded Python tool to edit Move Music save files\n"
            "and spawn VR interface panels on your desktop\n"
            "before putting on the headset.\n\n"
            "Move Music by Tim @ https://movemusic.com/\n"
        )
