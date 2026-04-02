"""Interface spawner – creates desktop windows that mirror the Move Music VR UI.

Use these before putting on the headset so you can preview and navigate the
in-game panels on your desktop monitor.
"""

from __future__ import annotations
import math
import tkinter as tk
from tkinter import ttk
from typing import List, Optional

from editor.save_file import SaveFile

# ── Colour palette (mirrors Move Music's neon/dark theme) ─────────────────────
BG_DARK = "#0d0d1a"
BG_PANEL = "#14142b"
ACCENT = "#7c3aed"       # purple
ACCENT2 = "#06b6d4"      # cyan
TEXT_BRIGHT = "#f0f0ff"
TEXT_DIM = "#888aaa"
HIT_GREEN = "#22c55e"
MISS_RED = "#ef4444"
GOLD = "#fbbf24"


def _centered_geometry(win: tk.Toplevel, width: int, height: int, offset_x: int = 0, offset_y: int = 0) -> None:
    win.update_idletasks()
    sw = win.winfo_screenwidth()
    sh = win.winfo_screenheight()
    x = (sw - width) // 2 + offset_x
    y = (sh - height) // 2 + offset_y
    win.geometry(f"{width}x{height}+{x}+{y}")


# ── Individual panel windows ──────────────────────────────────────────────────

class MainMenuPanel(tk.Toplevel):
    """Simulates the Move Music main-menu panel."""

    def __init__(self, master: tk.Widget, save: SaveFile) -> None:
        super().__init__(master)
        self.title("Move Music – Main Menu")
        self.configure(bg=BG_DARK)
        self.resizable(False, False)
        _centered_geometry(self, 480, 560, offset_x=-260)
        self._save = save
        self._build()

    def _build(self) -> None:
        # Header banner
        hdr = tk.Canvas(self, width=480, height=80, bg=ACCENT, highlightthickness=0)
        hdr.pack(fill="x")
        hdr.create_text(240, 40, text="🎵  Move Music", font=("Helvetica", 24, "bold"),
                        fill=TEXT_BRIGHT)

        # Player card
        card = tk.Frame(self, bg=BG_PANEL, bd=0)
        card.pack(fill="x", padx=20, pady=12)
        tk.Label(card, text=self._save.player.username, font=("Helvetica", 18, "bold"),
                 fg=TEXT_BRIGHT, bg=BG_PANEL).pack(pady=(10, 2))
        tk.Label(card, text=f"Level {self._save.player.level}  ·  {self._save.player.xp:,} XP",
                 font=("Helvetica", 11), fg=ACCENT2, bg=BG_PANEL).pack(pady=(0, 10))

        # XP bar
        xp_frame = tk.Frame(card, bg=BG_PANEL)
        xp_frame.pack(fill="x", padx=12, pady=(0, 12))
        xp_canvas = tk.Canvas(xp_frame, height=12, bg="#2a2a4a", highlightthickness=0)
        xp_canvas.pack(fill="x")
        # approximate fill – assume each level needs 1000 XP
        pct = min(1.0, (self._save.player.xp % 1000) / 1000)
        xp_canvas.update_idletasks()
        xp_canvas.bind("<Configure>", lambda e, c=xp_canvas, p=pct: (
            c.delete("bar"),
            c.create_rectangle(0, 0, e.width * p, 12, fill=ACCENT, outline="", tags="bar")
        ))

        # Nav buttons
        btns = [("▶  Play", ACCENT), ("🎵  Song List", ACCENT2),
                ("🏆  Leaderboard", GOLD), ("⚙️  Settings", "#6b7280")]
        for label, color in btns:
            btn = tk.Button(self, text=label, font=("Helvetica", 13, "bold"),
                            fg=TEXT_BRIGHT, bg=color, activebackground=color,
                            relief="flat", padx=12, pady=10, bd=0,
                            cursor="hand2", command=lambda: None)
            btn.pack(fill="x", padx=24, pady=5)

        # Footer
        tk.Label(self, text="movemusic.com", font=("Helvetica", 9),
                 fg=TEXT_DIM, bg=BG_DARK).pack(side="bottom", pady=8)


class SongListPanel(tk.Toplevel):
    """Simulates the Move Music song-selection panel."""

    def __init__(self, master: tk.Widget, save: SaveFile) -> None:
        super().__init__(master)
        self.title("Move Music – Song List")
        self.configure(bg=BG_DARK)
        self.resizable(False, False)
        _centered_geometry(self, 520, 620, offset_x=260)
        self._save = save
        self._build()

    def _build(self) -> None:
        hdr = tk.Canvas(self, width=520, height=60, bg=ACCENT2, highlightthickness=0)
        hdr.pack(fill="x")
        hdr.create_text(260, 30, text="🎶  Song Library", font=("Helvetica", 18, "bold"),
                        fill=BG_DARK)

        scroll_frame = tk.Frame(self, bg=BG_DARK)
        scroll_frame.pack(fill="both", expand=True, padx=10, pady=10)

        canvas = tk.Canvas(scroll_frame, bg=BG_DARK, highlightthickness=0)
        vsb = tk.Scrollbar(scroll_frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas, bg=BG_DARK)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfigure(win_id, width=canvas.winfo_width())

        inner.bind("<Configure>", _on_configure)
        canvas.bind("<Configure>", _on_configure)

        for i, song in enumerate(self._save.songs):
            row_bg = BG_PANEL if i % 2 == 0 else "#1a1a30"
            row = tk.Frame(inner, bg=row_bg, pady=2)
            row.pack(fill="x", padx=4, pady=2)

            lock = "✅" if song.unlocked else "🔒"
            tk.Label(row, text=lock, font=("Helvetica", 14), bg=row_bg).pack(side="left", padx=6)
            info = tk.Frame(row, bg=row_bg)
            info.pack(side="left", fill="x", expand=True)
            tk.Label(info, text=song.title, font=("Helvetica", 12, "bold"),
                     fg=TEXT_BRIGHT, bg=row_bg, anchor="w").pack(fill="x")
            tk.Label(info, text=song.artist, font=("Helvetica", 10),
                     fg=TEXT_DIM, bg=row_bg, anchor="w").pack(fill="x")
            score_text = f"🏆 {song.high_score:,}   🎯 {song.accuracy:.0%}"
            tk.Label(row, text=score_text, font=("Helvetica", 10),
                     fg=GOLD, bg=row_bg).pack(side="right", padx=10)


class ScoreboardPanel(tk.Toplevel):
    """Simulates the in-game score/HUD panel."""

    def __init__(self, master: tk.Widget, save: SaveFile) -> None:
        super().__init__(master)
        self.title("Move Music – Score HUD")
        self.configure(bg=BG_DARK)
        self.resizable(False, False)
        _centered_geometry(self, 400, 480, offset_y=-100)
        self._save = save
        self._score = 0
        self._combo = 0
        self._accuracy = 1.0
        self._build()
        self._animate_pulse()

    def _build(self) -> None:
        # Score display
        self._score_label = tk.Label(self, text="0", font=("Courier", 48, "bold"),
                                     fg=ACCENT2, bg=BG_DARK)
        self._score_label.pack(pady=(30, 0))
        tk.Label(self, text="SCORE", font=("Helvetica", 10), fg=TEXT_DIM, bg=BG_DARK).pack()

        # Combo counter
        self._combo_label = tk.Label(self, text="×0", font=("Helvetica", 32, "bold"),
                                     fg=GOLD, bg=BG_DARK)
        self._combo_label.pack(pady=(20, 0))
        tk.Label(self, text="COMBO", font=("Helvetica", 10), fg=TEXT_DIM, bg=BG_DARK).pack()

        # Accuracy ring (drawn on canvas)
        self._ring_canvas = tk.Canvas(self, width=160, height=160, bg=BG_DARK,
                                      highlightthickness=0)
        self._ring_canvas.pack(pady=16)
        self._draw_accuracy_ring(1.0)
        tk.Label(self, text="ACCURACY", font=("Helvetica", 10), fg=TEXT_DIM, bg=BG_DARK).pack()

        # Hit / Miss counters
        hm = tk.Frame(self, bg=BG_DARK)
        hm.pack(pady=12)
        tk.Label(hm, text="PERFECT  ", font=("Helvetica", 13), fg=HIT_GREEN, bg=BG_DARK).pack(side="left")
        tk.Label(hm, text="0", font=("Helvetica", 13, "bold"), fg=HIT_GREEN, bg=BG_DARK).pack(side="left")
        tk.Label(hm, text="    MISS  ", font=("Helvetica", 13), fg=MISS_RED, bg=BG_DARK).pack(side="left")
        tk.Label(hm, text="0", font=("Helvetica", 13, "bold"), fg=MISS_RED, bg=BG_DARK).pack(side="left")

        # Demo: simulate score ticking
        tk.Button(self, text="▶  Simulate", bg=ACCENT, fg=TEXT_BRIGHT,
                  relief="flat", padx=10, pady=6, bd=0, font=("Helvetica", 11),
                  command=self._start_simulation).pack(pady=4)

    def _draw_accuracy_ring(self, accuracy: float) -> None:
        self._ring_canvas.delete("all")
        cx, cy, r = 80, 80, 64
        # Background ring
        self._ring_canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                                      outline="#2a2a4a", width=14)
        # Accuracy arc
        extent = accuracy * 359.9
        color = HIT_GREEN if accuracy >= 0.9 else (GOLD if accuracy >= 0.7 else MISS_RED)
        self._ring_canvas.create_arc(cx - r, cy - r, cx + r, cy + r,
                                     start=90, extent=-extent,
                                     outline=color, width=14, style="arc")
        self._ring_canvas.create_text(cx, cy, text=f"{accuracy:.0%}",
                                      font=("Helvetica", 20, "bold"), fill=TEXT_BRIGHT)

    def _start_simulation(self) -> None:
        """Animate a fake score run for demo purposes."""
        self._score = 0
        self._combo = 0
        self._tick_count = 0
        self._simulate_tick()

    def _simulate_tick(self) -> None:
        if self._tick_count >= 60:
            return
        self._score += 500 + self._combo * 10
        self._combo += 1
        self._accuracy = max(0.8, self._accuracy - 0.001)
        self._score_label.config(text=f"{self._score:,}")
        self._combo_label.config(text=f"×{self._combo}")
        self._draw_accuracy_ring(self._accuracy)
        self._tick_count += 1
        self.after(80, self._simulate_tick)

    def _animate_pulse(self) -> None:
        """Gently pulse the score label colour to indicate live panel."""
        colors = [ACCENT2, "#0ea5e9", "#38bdf8", "#0ea5e9", ACCENT2]
        idx = getattr(self, "_pulse_idx", 0) % len(colors)
        self._score_label.config(fg=colors[idx])
        self._pulse_idx = idx + 1
        self.after(600, self._animate_pulse)


class ControllerPanel(tk.Toplevel):
    """Visual representation of a VR controller (left or right)."""

    def __init__(self, master: tk.Widget, side: str = "right") -> None:
        super().__init__(master)
        self.title(f"Move Music – {side.capitalize()} Controller")
        self.configure(bg=BG_DARK)
        self.resizable(False, False)
        self._side = side
        offset_x = 500 if side == "right" else -500
        _centered_geometry(self, 300, 420, offset_x=offset_x, offset_y=80)
        self._canvas = tk.Canvas(self, width=300, height=380, bg=BG_DARK,
                                 highlightthickness=0)
        self._canvas.pack()
        self._angle = 0.0
        self._draw_controller()
        self._animate()

    def _draw_controller(self) -> None:
        c = self._canvas
        c.delete("all")
        cx, cy = 150, 200

        # Controller body (rounded rect approximation)
        c.create_oval(cx - 55, cy - 120, cx + 55, cy + 100,
                      fill=BG_PANEL, outline=ACCENT, width=3)

        # Thumbstick – show slight animated drift
        drift_x = int(20 * math.sin(self._angle))
        drift_y = int(10 * math.cos(self._angle * 0.7))
        stick_cx, stick_cy = cx + drift_x, cy - 60 + drift_y
        c.create_oval(cx - 30, cy - 90, cx + 30, cy - 30,
                      fill="#2a2a4a", outline=ACCENT2, width=2)
        c.create_oval(stick_cx - 12, stick_cy - 12, stick_cx + 12, stick_cy + 12,
                      fill=ACCENT2, outline=TEXT_BRIGHT, width=1)

        # Trigger (top)
        c.create_rectangle(cx - 30, cy - 150, cx + 30, cy - 115,
                            fill=ACCENT, outline=TEXT_BRIGHT, width=1)
        c.create_text(cx, cy - 133, text="TRIGGER", font=("Helvetica", 7, "bold"),
                      fill=TEXT_BRIGHT)

        # A/B or X/Y buttons (side-specific)
        labels = ("A", "B") if self._side == "right" else ("X", "Y")
        colors = (HIT_GREEN, GOLD) if self._side == "right" else (ACCENT2, "#f472b6")
        for i, (lbl, col) in enumerate(zip(labels, colors)):
            bx = (cx + 35) if i == 0 else (cx + 15)
            by = (cy + 10) if i == 0 else (cy - 10)
            c.create_oval(bx - 13, by - 13, bx + 13, by + 13,
                          fill=col, outline=TEXT_BRIGHT, width=1)
            c.create_text(bx, by, text=lbl, font=("Helvetica", 10, "bold"),
                          fill=TEXT_BRIGHT)

        # Grip
        c.create_rectangle(cx - 40, cy + 50, cx + 40, cy + 70,
                            fill="#2a2a4a", outline=ACCENT, width=1)
        c.create_text(cx, cy + 60, text="GRIP", font=("Helvetica", 8),
                      fill=TEXT_DIM)

        # Haptic indicator (pulsing dot)
        pulse_r = 6 + int(3 * abs(math.sin(self._angle * 2)))
        c.create_oval(cx - pulse_r, cy + 85 - pulse_r,
                      cx + pulse_r, cy + 85 + pulse_r,
                      fill=ACCENT, outline="")

        # Label
        side_label = "RIGHT  ▶" if self._side == "right" else "◀  LEFT"
        c.create_text(cx, 350, text=side_label, font=("Helvetica", 11, "bold"),
                      fill=ACCENT)

    def _animate(self) -> None:
        self._angle += 0.05
        self._draw_controller()
        self.after(50, self._animate)


# ── Spawner orchestrator ──────────────────────────────────────────────────────

class InterfaceSpawner:
    """Opens all Move Music VR panels as desktop windows."""

    _PANEL_LABELS = {
        "main_menu": "Main Menu",
        "song_list": "Song List",
        "scoreboard": "Score HUD",
        "left_ctrl": "Left Controller",
        "right_ctrl": "Right Controller",
    }

    def __init__(self, master: tk.Widget, save: SaveFile) -> None:
        self._master = master
        self._save = save
        self._open_panels: dict[str, Optional[tk.Toplevel]] = {}

    def spawn_all(self) -> None:
        """Open every interface panel."""
        for key in self._PANEL_LABELS:
            self.spawn(key)

    def close_all(self) -> None:
        """Close all spawned panels."""
        for win in list(self._open_panels.values()):
            if win and win.winfo_exists():
                win.destroy()
        self._open_panels.clear()

    def spawn(self, key: str) -> Optional[tk.Toplevel]:
        """Open a single panel by key, reusing it if already open."""
        if key in self._open_panels:
            existing = self._open_panels[key]
            if existing and existing.winfo_exists():
                existing.lift()
                return existing

        win: Optional[tk.Toplevel] = None
        if key == "main_menu":
            win = MainMenuPanel(self._master, self._save)
        elif key == "song_list":
            win = SongListPanel(self._master, self._save)
        elif key == "scoreboard":
            win = ScoreboardPanel(self._master, self._save)
        elif key == "left_ctrl":
            win = ControllerPanel(self._master, side="left")
        elif key == "right_ctrl":
            win = ControllerPanel(self._master, side="right")

        if win:
            win.protocol("WM_DELETE_WINDOW", lambda k=key, w=win: self._on_close(k, w))
            self._open_panels[key] = win
        return win

    def _on_close(self, key: str, win: tk.Toplevel) -> None:
        win.destroy()
        self._open_panels.pop(key, None)

    @property
    def panel_labels(self) -> dict[str, str]:
        return dict(self._PANEL_LABELS)
