"""Save editor tab – lets the user view and edit Move Music save data."""

from __future__ import annotations
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable, Optional

from editor.save_file import SaveFile
from editor.models import SongEntry


class SaveEditorTab(ttk.Frame):
    """Tab widget containing the full save-file editing UI."""

    def __init__(self, parent: tk.Widget, save: SaveFile, on_change: Optional[Callable] = None) -> None:
        super().__init__(parent)
        self._save = save
        self._on_change = on_change
        self._build_ui()

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # ── Player section ────────────────────────────────────────────
        player_frame = ttk.LabelFrame(self, text="  Player Info  ", padding=10)
        player_frame.grid(row=0, column=0, padx=12, pady=(12, 6), sticky="ew")

        ttk.Label(player_frame, text="Username:").grid(row=0, column=0, sticky="w", padx=4)
        self._username_var = tk.StringVar(value=self._save.player.username)
        ttk.Entry(player_frame, textvariable=self._username_var, width=24).grid(row=0, column=1, padx=6, pady=2)

        ttk.Label(player_frame, text="Level:").grid(row=0, column=2, sticky="w", padx=4)
        self._level_var = tk.IntVar(value=self._save.player.level)
        ttk.Spinbox(player_frame, from_=1, to=999, textvariable=self._level_var, width=8).grid(row=0, column=3, padx=6, pady=2)

        ttk.Label(player_frame, text="XP:").grid(row=1, column=0, sticky="w", padx=4)
        self._xp_var = tk.IntVar(value=self._save.player.xp)
        ttk.Entry(player_frame, textvariable=self._xp_var, width=14).grid(row=1, column=1, padx=6, pady=2)

        ttk.Label(player_frame, text="Total Score:").grid(row=1, column=2, sticky="w", padx=4)
        self._total_score_var = tk.IntVar(value=self._save.player.total_score)
        ttk.Entry(player_frame, textvariable=self._total_score_var, width=14).grid(row=1, column=3, padx=6, pady=2)

        # ── Songs section ─────────────────────────────────────────────
        songs_outer = ttk.LabelFrame(self, text="  Songs  ", padding=10)
        songs_outer.grid(row=1, column=0, padx=12, pady=6, sticky="nsew")
        songs_outer.rowconfigure(1, weight=1)
        songs_outer.columnconfigure(0, weight=1)

        # Quick action buttons
        btn_row = ttk.Frame(songs_outer)
        btn_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(btn_row, text="✅  Unlock All", command=self._unlock_all).pack(side="left", padx=4)
        ttk.Button(btn_row, text="🔄  Reset Scores", command=self._reset_scores).pack(side="left", padx=4)

        # Treeview
        cols = ("title", "artist", "unlocked", "high_score", "accuracy", "plays")
        self._tree = ttk.Treeview(songs_outer, columns=cols, show="headings", height=10)
        col_widths = {"title": 180, "artist": 160, "unlocked": 70, "high_score": 90, "accuracy": 80, "plays": 60}
        for col in cols:
            self._tree.heading(col, text=col.replace("_", " ").title())
            self._tree.column(col, width=col_widths.get(col, 100), anchor="center" if col not in ("title", "artist") else "w")

        vsb = ttk.Scrollbar(songs_outer, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.grid(row=1, column=0, sticky="nsew")
        vsb.grid(row=1, column=1, sticky="ns")
        self._tree.bind("<Double-1>", self._on_song_double_click)

        self._refresh_song_list()

        # ── Settings section ──────────────────────────────────────────
        settings_frame = ttk.LabelFrame(self, text="  Game Settings  ", padding=10)
        settings_frame.grid(row=2, column=0, padx=12, pady=6, sticky="ew")

        self._music_vol_var = tk.DoubleVar(value=self._save.settings.music_volume)
        self._sfx_vol_var = tk.DoubleVar(value=self._save.settings.sfx_volume)
        self._comfort_var = tk.BooleanVar(value=self._save.settings.comfort_mode)
        self._haptics_var = tk.BooleanVar(value=self._save.settings.controller_haptics)
        self._hand_track_var = tk.BooleanVar(value=self._save.settings.hand_tracking)
        self._mirror_var = tk.BooleanVar(value=self._save.settings.mirror_display)

        def _make_slider(parent, label, var, col_offset):
            ttk.Label(parent, text=label).grid(row=0, column=col_offset, sticky="w", padx=4)
            ttk.Scale(parent, from_=0.0, to=1.0, orient="horizontal",
                      variable=var, length=110).grid(row=0, column=col_offset + 1, padx=4)

        _make_slider(settings_frame, "🎵 Music Vol:", self._music_vol_var, 0)
        _make_slider(settings_frame, "🔊 SFX Vol:", self._sfx_vol_var, 2)

        chk_frame = ttk.Frame(settings_frame)
        chk_frame.grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Checkbutton(chk_frame, text="Comfort Mode", variable=self._comfort_var).pack(side="left", padx=8)
        ttk.Checkbutton(chk_frame, text="Controller Haptics", variable=self._haptics_var).pack(side="left", padx=8)
        ttk.Checkbutton(chk_frame, text="Hand Tracking", variable=self._hand_track_var).pack(side="left", padx=8)
        ttk.Checkbutton(chk_frame, text="Mirror Display", variable=self._mirror_var).pack(side="left", padx=8)

        # ── Apply button ──────────────────────────────────────────────
        ttk.Button(self, text="💾  Apply Changes", command=self._apply_changes).grid(
            row=3, column=0, pady=(6, 12)
        )

    # ------------------------------------------------------------------

    def _refresh_song_list(self) -> None:
        self._tree.delete(*self._tree.get_children())
        for song in self._save.songs:
            lock_icon = "✅" if song.unlocked else "🔒"
            self._tree.insert("", "end", iid=song.id, values=(
                song.title,
                song.artist,
                lock_icon,
                f"{song.high_score:,}",
                f"{song.accuracy:.1%}",
                song.plays,
            ))

    def _unlock_all(self) -> None:
        self._save.unlock_all_songs()
        self._refresh_song_list()
        if self._on_change:
            self._on_change()

    def _reset_scores(self) -> None:
        if messagebox.askyesno("Reset Scores", "Reset all song scores to zero?"):
            self._save.reset_scores()
            self._refresh_song_list()
            if self._on_change:
                self._on_change()

    def _on_song_double_click(self, event) -> None:
        selection = self._tree.selection()
        if not selection:
            return
        song_id = selection[0]
        song = self._save.get_song_by_id(song_id)
        if song:
            self._open_song_editor(song)

    def _open_song_editor(self, song: SongEntry) -> None:
        win = tk.Toplevel(self)
        win.title(f"Edit Song – {song.title}")
        win.resizable(False, False)
        win.grab_set()

        f = ttk.Frame(win, padding=16)
        f.pack(fill="both", expand=True)

        fields = [
            ("Title", "title", str),
            ("Artist", "artist", str),
            ("High Score", "high_score", int),
            ("Accuracy (0–1)", "accuracy", float),
            ("Plays", "plays", int),
            ("Easy Score", None, int),
            ("Normal Score", None, int),
            ("Hard Score", None, int),
            ("Expert Score", None, int),
        ]

        vars_: list = []
        diff_keys = ["easy", "normal", "hard", "expert"]
        diff_idx = 0

        for i, (label, attr, type_) in enumerate(fields):
            ttk.Label(f, text=label + ":").grid(row=i, column=0, sticky="w", padx=6, pady=3)
            if attr is not None:
                val = getattr(song, attr)
                diff_key = None
            else:
                diff_key = diff_keys[diff_idx]
                val = getattr(song.difficulty_scores, diff_key)
                diff_idx += 1
            var = tk.StringVar(value=str(val))
            vars_.append((var, attr, type_, diff_key))
            ttk.Entry(f, textvariable=var, width=22).grid(row=i, column=1, padx=6, pady=3)

        # Unlock toggle
        unlock_var = tk.BooleanVar(value=song.unlocked)
        ttk.Checkbutton(f, text="Unlocked", variable=unlock_var).grid(
            row=len(fields), column=0, columnspan=2, pady=4
        )

        def _save_song():
            try:
                for var, attr, type_, diff_key in vars_:
                    value = type_(var.get())
                    if attr is not None:
                        setattr(song, attr, value)
                    else:
                        setattr(song.difficulty_scores, diff_key, value)
                song.unlocked = unlock_var.get()
            except ValueError as e:
                messagebox.showerror("Invalid Value", str(e), parent=win)
                return
            self._refresh_song_list()
            if self._on_change:
                self._on_change()
            win.destroy()

        ttk.Button(f, text="Save", command=_save_song).grid(
            row=len(fields) + 1, column=0, columnspan=2, pady=8
        )

    def _apply_changes(self) -> None:
        try:
            self._save.player.username = self._username_var.get()
            self._save.player.level = self._level_var.get()
            self._save.player.xp = self._xp_var.get()
            self._save.player.total_score = self._total_score_var.get()
            self._save.settings.music_volume = round(self._music_vol_var.get(), 2)
            self._save.settings.sfx_volume = round(self._sfx_vol_var.get(), 2)
            self._save.settings.comfort_mode = self._comfort_var.get()
            self._save.settings.controller_haptics = self._haptics_var.get()
            self._save.settings.hand_tracking = self._hand_track_var.get()
            self._save.settings.mirror_display = self._mirror_var.get()
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror("Invalid Value", str(exc))
            return
        if self._on_change:
            self._on_change()
        messagebox.showinfo("Changes Applied", "All changes have been applied.\nRemember to save the file!")

    def reload(self) -> None:
        """Reload UI fields from the current save object."""
        self._username_var.set(self._save.player.username)
        self._level_var.set(self._save.player.level)
        self._xp_var.set(self._save.player.xp)
        self._total_score_var.set(self._save.player.total_score)
        self._music_vol_var.set(self._save.settings.music_volume)
        self._sfx_vol_var.set(self._save.settings.sfx_volume)
        self._comfort_var.set(self._save.settings.comfort_mode)
        self._haptics_var.set(self._save.settings.controller_haptics)
        self._hand_track_var.set(self._save.settings.hand_tracking)
        self._mirror_var.set(self._save.settings.mirror_display)
        self._refresh_song_list()
