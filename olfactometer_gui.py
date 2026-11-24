"""Tk GUI replacing the LabVIEW olfactometer VI.

1. Start acquisition  -> <output>/<stamp>.tdms  (AI, like LabVIEW)
2. Load the experiment .txt from matlab/create_exp_matlab.m, Run
   -> valves over RS485 + DO pattern/counter, logs <output>/<txt>_<stamp>_{NonTimedDO,TimedDO}.txt
3. Stop experiment / Stop acquisition. Valves return to blank on stop, error or close.

Rig settings live in rig.json next to this file and are saved back on Run.
"""
import json
import os
import queue
import re
import threading
import time
import tkinter as tk
from collections import deque
from tkinter import filedialog, messagebox, ttk

import numpy as np
from serial.tools import list_ports

from experiment_runner import (DryRunOlfactometer, SerialOlfactometer, all_blank,
                               run_experiment, stamp)
from io_utils import read_experiment_file
from novelty import DEFAULT_DIRS, OdorList, odor_ids

RIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rig.json")
DRY = "dry-run (no hardware)"
FIELDS = [("serial_port", "Serial port"), ("board_ids", "Board IDs (board 1, 2, ...)"),
          ("ai_channels", "AI channels"), ("ai_fs", "AI rate [Hz]"), ("ai_terminal", "AI terminal"),
          ("pattern_lines", "16-bit pattern DO"), ("counter_lines", "7-bit counter DO"),
          ("output_dir", "Output folder")]
WINDOWS = (2, 5, 10, 30, 60)  # s shown; Acquisition keeps 60 s of AI
MIN_SPAN = 0.02  # V; smallest vertical scale of a trace
SMALL, MONO = ("TkDefaultFont", 8), ("Consolas", 10)
# scope colours: analog blue on shaded rows; digital lanes as filled blocks, one colour per group
EVENT, PAUSE = "#d62728", "#e07b00"
FACECAM, ODOR, PATTERN, COUNTER = "#7b3fbf", "#f28e2b", "#3a9d4b", "#5b6abf"
STATES = {"IDLE": "#8a8a8a", "WAITING": "#2f6db5", "RUNNING": "#2e8b3e", "PAUSED": "#e07b00",
          "FINISHED": "#4d4d4d", "STOPPED": "#b03a2e", "ERROR": "#c00000"}
ROLE_COLORS = {"novel": "#c2185b", "familiar": "#00897b", "blank": "#546e7a"}  # anything else: grey


def line_names(spec):
    """'Dev1/port1/line0:7,Dev1/port2/line0:7' -> ['P1.0', ..., 'P2.7'] (None if the spec is not port/line)."""
    names = []
    for part in spec.split(","):
        m = re.search(r"port(\d+)/line(\d+)(?::(\d+))?", part)
        if not m:
            return None
        a, b = int(m[2]), int(m[3] if m[3] else m[2])
        names += [f"P{m[1]}.{k}" for k in range(a, b + (1 if b >= a else -1), 1 if b >= a else -1)]
    return names


def char_role(k):
    """Pattern character k (1-based): name and lane colour."""
    if k == 1:
        return "facecam", FACECAM
    if 8 <= k <= 15:
        return f"odor b{15 - k}", ODOR
    return "", PATTERN


class Scope(ttk.LabelFrame):
    """Analog traces and digital lanes on one time axis (now on the right), with events and pauses marked."""
    LEFT, RIGHT, TOP, AXIS = 132, 8, 14, 18

    def __init__(self, master):
        super().__init__(master, text="Signals")
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=4, pady=2)
        ttk.Label(bar, text="window [s]").pack(side="left")
        self.window = tk.IntVar(value=10)
        ttk.Combobox(bar, textvariable=self.window, values=WINDOWS, width=4, state="readonly").pack(side="left")
        self.shared = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="same scale for all analog channels", variable=self.shared).pack(side="left", padx=10)
        self.scale = ttk.Label(bar, text="", foreground="gray")
        self.scale.pack(side="left")
        for text, color in (("facecam", FACECAM), ("odor ID", ODOR), ("pattern", PATTERN), ("counter", COUNTER),
                            ("event", EVENT), ("pause", PAUSE)):
            tk.Label(bar, text=f" {text} ", bg=color, fg="white", font=SMALL).pack(side="right", padx=1)
        self.digital = tk.Canvas(self, width=700, height=44, background="#fafafa", highlightthickness=0)
        self.digital.pack(side="bottom", fill="x")
        self.analog = tk.Canvas(self, width=700, height=430, background="white", highlightthickness=0)
        self.analog.pack(fill="both", expand=True)

    @staticmethod
    def size(c):
        W, H = c.winfo_width(), c.winfo_height()
        return (W, H) if W > 50 else (int(c["width"]), int(c["height"]))  # not mapped yet

    def frame(self, c, now, markers, labels):
        """Clear `c`, draw the time grid and markers; return (W, H, x_of)."""
        c.delete("all")
        W, H = self.size(c)
        win, x1 = self.window.get(), W - self.RIGHT
        px = max(x1 - self.LEFT, 1)
        x_of = lambda t: x1 - (now - t) / win * px  # noqa: E731  perf_counter time -> x
        for s in range(0, win + 1, max(win // 5, 1)):
            x = x1 - s / win * px
            c.create_line(x, self.TOP, x, H - self.AXIS, fill="#e6e6e6")
            c.create_text(x, H - 9, text=f"-{s} s" if s else "now", fill="gray", font=SMALL)
        for t, label in markers:
            if now - win <= t <= now:
                color = EVENT if label.isdigit() else PAUSE
                c.create_line(x_of(t), self.TOP, x_of(t), H - self.AXIS, fill=color, dash=(4, 3), width=1)
                if labels:
                    c.create_text(x_of(t), self.TOP - 7, text=label, fill=color, font=("TkDefaultFont", 8, "bold"))
        return W, H, x_of

    def draw_analog(self, now, markers, acq):
        c = self.analog
        W, H, x_of = self.frame(c, now, markers, labels=True)
        if acq is None or acq.n_samples < 2:
            c.create_text(W / 2, H / 2, text="no acquisition", fill="gray", font=("TkDefaultFont", 11))
            self.scale.configure(text="")
            return
        data = acq.recent_data(self.window.get())
        names = [n.split("/")[-1].upper() for n in acq.names]
        n_ch, n = data.shape
        row = (H - self.TOP - self.AXIS) / n_ch
        t_last = getattr(acq, "t_chunk", now)
        # min/max per pixel column, so short pulses stay visible however long the window
        x_first, x_last = x_of(t_last - (n - 1) / acq.fs), x_of(t_last)
        cols = max(min(int(x_last - x_first), n), 1)
        edges = np.unique(np.linspace(0, n, cols + 1).astype(int)[:-1])
        mins, maxs = np.minimum.reduceat(data, edges, axis=1), np.maximum.reduceat(data, edges, axis=1)
        xs = x_of(t_last - (n - 1 - edges) / acq.fs)
        lo_all, hi_all = data.min(), data.max()
        self.scale.configure(text=f"scale {lo_all:+.3f} .. {hi_all:+.3f} V" if self.shared.get() else "")
        for k in range(n_ch):
            top = self.TOP + k * row
            if k % 2:
                c.create_rectangle(0, top, W, top + row, fill="#eef3fa", outline="", tags="shade")
            lo, hi = (lo_all, hi_all) if self.shared.get() else (mins[k].min(), maxs[k].max())
            if hi - lo < MIN_SPAN:  # quiet channels stay flat instead of blowing their noise up to full height
                mid = (hi + lo) / 2
                lo, hi = mid - MIN_SPAN / 2, mid + MIN_SPAN / 2
            y = lambda v: top + row - 2 - (v - lo) / (hi - lo) * (row - 4)  # noqa: E731
            pts = np.empty((len(xs) * 2, 2))
            pts[0::2, 0] = pts[1::2, 0] = xs
            pts[0::2, 1], pts[1::2, 1] = y(mins[k]), y(maxs[k])
            c.create_line(*pts.ravel(), fill="#1f4e9c")
            c.create_text(6, top + row / 2, anchor="w", font=MONO, text=f"{names[k]:<5}{data[k, -1]:+.3f} V")
        c.tag_lower("shade")  # under the grid and markers

    def draw_digital(self, pattern, counter, pattern_names, counter_names):
        """One dot per DO line, lit in its group's colour while high. Above: character / counter bit; below: line."""
        c = self.digital
        c.delete("all")
        W, H = self.size(c)
        dots = [(str(k), pattern_names[k - 1], char_role(k)[1], pattern[k - 1] == "1") for k in range(1, 17)]
        dots += [(f"c{b}", counter_names[b], COUNTER, bool(counter >> b & 1)) for b in range(7)]
        gap = 14  # between the pattern and counter groups
        step = min(30, (W - self.LEFT - self.RIGHT - gap) / len(dots))
        c.create_text(6, H / 2 - 7, anchor="w", text="DO pattern", font=SMALL)
        c.create_text(6, H / 2 + 7, anchor="w", text=pattern if len(pattern) == 16 else "", font=("Consolas", 8))
        for j, (top, bottom, color, on) in enumerate(dots):
            x = self.LEFT + step * (j + 0.5) + (gap if j >= 16 else 0)
            r = 6
            c.create_oval(x - r, H / 2 - r, x + r, H / 2 + r, fill=color if on else "#dedede",
                          outline=color if on else "#bdbdbd")
            c.create_text(x, H / 2 - r - 7, text=top, font=("TkDefaultFont", 7), fill=color)
            c.create_text(x, H / 2 + r + 7, text=bottom, font=("TkDefaultFont", 6), fill="gray")


class Status(ttk.LabelFrame):
    """Big state banner, clocks, current row and the next rows with countdowns."""

    def __init__(self, master):
        super().__init__(master, text="Experiment state")
        self.banner = tk.Label(self, text="IDLE", font=("Segoe UI", 15, "bold"), fg="white", bg=STATES["IDLE"])
        self.banner.pack(fill="x", padx=4, pady=(2, 3))
        grid = ttk.Frame(self)
        grid.pack(fill="x", padx=6)
        self.values = {}
        for r, key in enumerate(("protocol time", "elapsed", "event", "current row", "valves open")):
            ttk.Label(grid, text=key, foreground="gray").grid(row=r, column=0, sticky="w", padx=(0, 10))
            v = ttk.Label(grid, text="-", font=("Consolas", 12 if r < 3 else 10, "bold" if r < 3 else "normal"))
            v.grid(row=r, column=1, sticky="w")
            self.values[key] = v
        ttk.Label(grid, text="odor", foreground="gray").grid(row=5, column=0, sticky="w", padx=(0, 10))
        self.odor = tk.Label(grid, text="-", font=("Segoe UI", 11, "bold"), padx=6)
        self.odor.grid(row=5, column=1, sticky="w", pady=(2, 0))
        self.plain = self.odor.cget("background"), self.odor.cget("foreground")
        ttk.Label(self, text="next events", foreground="gray").pack(anchor="w", padx=6, pady=(3, 0))
        cols = ("#", "in", "at", "pattern", "valves", "odor")
        self.next = ttk.Treeview(self, columns=cols, show="headings", height=5)
        for col, w in zip(cols, (40, 64, 64, 150, 90, 90)):
            self.next.heading(col, text=col)
            self.next.column(col, width=w, anchor="w" if col in ("pattern", "valves", "odor") else "e", stretch=False)
        self.next.tag_configure("odor", background="#fde3c8")
        self.next.tag_configure("first", font=("TkDefaultFont", 9, "bold"))
        self.next.pack(fill="x", padx=6, pady=(0, 6))

    @staticmethod
    def clock(s):
        s = max(s, 0)
        return f"{int(s // 60):02d}:{s % 60:04.1f}"

    @staticmethod
    def valves(row):
        opened = [f"board {b} valve {v}" for b, v in row["valves"] if v != 1]
        return ", ".join(opened) or "all blank"

    def badge(self, text, ids=(), odors=None):
        """Odor line coloured by the odor list's role (novel / familiar / blank, grey if unknown); plain when no odor."""
        if not ids:
            self.odor.configure(text=text, bg=self.plain[0], fg=self.plain[1])
            return
        roles = {odors.role(i) if odors else "?" for i in ids}
        color = "#8a8a8a" if len(roles) != 1 else ROLE_COLORS.get(roles.pop(), "#8a8a8a")
        self.odor.configure(text=text, bg=color, fg="white")

    def show(self, state, rows=None, done=0, proto=0.0, elapsed=0.0, paused_s=0.0, odors=None):
        """`odors`: the session's novelty.OdorList, or None."""
        label = (lambda ids, chem=False: odors.label(ids, chem)) if odors else (
            lambda ids, chem=False: ", ".join(f"{i} ?" for i in ids))
        self.banner.configure(text=state, bg=STATES[state])
        v = self.values
        if not rows:
            for w in v.values():
                w.configure(text="-")
            self.badge("-")
            self.next.delete(*self.next.get_children())
            return
        v["protocol time"].configure(text=f"{self.clock(proto)} / {self.clock(rows[-1]['time'] / 1000)}")
        v["elapsed"].configure(text=self.clock(elapsed) + (f"  (paused {self.clock(paused_s)})" if paused_s else ""))
        v["event"].configure(text=f"{done} / {len(rows)}")
        cur = rows[done - 1] if done else None
        p = cur["pattern"] if cur else "0" * 16
        v["current row"].configure(text=f"{p[0]} {p[1:7]} {p[7:15]} {p[15]}   facecam {'ON' if p[0] == '1' else 'off'}"
                                   + (f"   odor ID {int(p[7:15], 2)}" if int(p[7:15], 2) else ""))
        v["valves open"].configure(text=self.valves(cur) if cur else "all blank")
        now_ids = odor_ids(cur) if cur and state == "RUNNING" else []  # paused / stopped: valves are blank
        upcoming = next(((j, r) for j, r in enumerate(rows[done:], done) if odor_ids(r)), None)
        if now_ids:
            self.badge(f"ON   {label(now_ids, True)}", now_ids, odors)
        elif upcoming and state in ("WAITING", "RUNNING", "PAUSED", "IDLE"):
            j, r = upcoming
            self.badge(f"next {label(odor_ids(r), True)} in {max(r['time'] / 1000 - proto, 0):.1f} s",
                       odor_ids(r), odors)
        else:
            self.badge("none")
        self.next.delete(*self.next.get_children())
        for j, r in enumerate(rows[done:done + 5], done + 1):
            tags = (("odor",) if any(val != 1 for _, val in r["valves"]) else ()) + (("first",) if j == done + 1 else ())
            self.next.insert("", "end", tags=tags, values=(
                j, f"{max(r['time'] / 1000 - proto, 0):.1f} s", self.clock(r["time"] / 1000),
                r["pattern"], self.valves(r).replace("board ", "b").replace(" valve ", " v"),
                label(odor_ids(r))))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Olfactometer")
        with open(RIG) as f:
            self.rig = json.load(f)
        self.olf = self.acq = self.run_thread = None
        self.stop, self.pause = threading.Event(), threading.Event()
        self.events = queue.Queue()

        settings = ttk.LabelFrame(self, text="Rig")
        settings.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self.vars = {}
        for r, (key, label) in enumerate(FIELDS):
            ttk.Label(settings, text=label).grid(row=r, column=0, sticky="w")
            value = self.rig.get(key, "")
            v = tk.StringVar(value=", ".join(value) if isinstance(value, list) else str(value))
            self.vars[key] = v
            if key == "serial_port":
                w = ttk.Combobox(settings, textvariable=v, width=40,
                                 values=[p.device for p in list_ports.comports()] + [DRY])
            else:
                w = ttk.Entry(settings, textvariable=v, width=42)
            w.grid(row=r, column=1, sticky="ew")
        ttk.Button(settings, text="...", width=3, command=self.pick_output).grid(row=len(FIELDS) - 1, column=2)
        self.use_daq = tk.BooleanVar(value=self.rig.get("use_daq", True))
        ttk.Checkbutton(settings, text="Use NI-DAQ (acquisition + DO)", variable=self.use_daq).grid(
            row=len(FIELDS), column=1, sticky="w")

        acq = ttk.LabelFrame(self, text="Acquisition")
        acq.grid(row=1, column=0, sticky="ew", padx=6)
        self.acq_btn = ttk.Button(acq, text="Start acquisition", command=self.toggle_acq)
        self.acq_btn.grid(row=0, column=0)
        self.acq_status = ttk.Label(acq, text="stopped")
        self.acq_status.grid(row=0, column=1, sticky="w", padx=6)

        exp = ttk.LabelFrame(self, text="Experiment")
        exp.grid(row=2, column=0, sticky="ew", padx=6, pady=6)
        self.txt = tk.StringVar()
        ttk.Entry(exp, textvariable=self.txt, width=50).grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Button(exp, text="Load .txt", command=self.pick_txt).grid(row=0, column=2)
        self.run_btn = ttk.Button(exp, text="Run", command=self.toggle_run)
        self.run_btn.grid(row=1, column=0, sticky="w")
        self.pause_btn = ttk.Button(exp, text="Pause", command=self.toggle_pause, state="disabled")
        self.pause_btn.grid(row=1, column=2, sticky="w")
        self.progress = ttk.Progressbar(exp, length=300)
        self.progress.grid(row=1, column=1, sticky="ew")
        ttk.Label(exp, text="odor list").grid(row=2, column=0, sticky="w")
        self.odor_list = tk.StringVar()  # odor_list_<mouse>week<n>.csv; found on load, or picked with "..."
        ttk.Entry(exp, textvariable=self.odor_list, width=40, state="readonly").grid(row=2, column=1, sticky="ew")
        ttk.Button(exp, text="...", width=3, command=self.pick_odor_list).grid(row=2, column=2, sticky="w")
        self.odor_check = tk.Label(exp, text="", font=SMALL, justify="left", anchor="w", wraplength=420)
        self.odor_check.grid(row=3, column=0, columnspan=3, sticky="w")
        self.odors, self.odors_for = None, None

        self.status = Status(self)
        self.status.grid(row=3, column=0, sticky="ew", padx=6)

        man = ttk.LabelFrame(self, text="Manual valve (flush / test)")
        man.grid(row=4, column=0, sticky="ew", padx=6, pady=6)
        self.m_board, self.m_valve = tk.IntVar(value=1), tk.IntVar(value=1)
        ttk.Label(man, text="board").grid(row=0, column=0)
        ttk.Spinbox(man, from_=1, to=16, width=4, textvariable=self.m_board).grid(row=0, column=1)
        ttk.Label(man, text="valve").grid(row=0, column=2)
        ttk.Spinbox(man, from_=1, to=8, width=4, textvariable=self.m_valve).grid(row=0, column=3)
        ttk.Button(man, text="Drive", command=self.manual).grid(row=0, column=4)
        ttk.Button(man, text="All blank", command=self.blank).grid(row=0, column=5)

        self.log = tk.Text(self, height=5, width=80, state="disabled")
        self.log.grid(row=5, column=0, sticky="nsew", padx=6, pady=(0, 6))
        self.scope = Scope(self)
        self.scope.grid(row=0, column=1, rowspan=6, sticky="nsew", padx=6, pady=6)
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=3)
        self.rowconfigure(5, weight=1)

        # run state, all in perf_counter seconds; filled by the worker's callbacks through self.events
        self.rows, self.done, self.state = [], 0, "IDLE"
        self.markers = deque(maxlen=400)  # (t, label): event numbers, "pause", "resume"
        self.history = deque(maxlen=50)  # (t, pattern, counter): what the DO lines were set to, newest last
        self.t_run = self.t0 = self.frozen = self.t_pause = None  # run start, protocol zero, frozen protocol time
        self.paused_s = 0.0
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(150, self.poll)

    # --- helpers ---
    def say(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", f"{stamp()}  {msg}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def settings(self):
        rig = {k: v.get().strip() for k, v in self.vars.items()}
        rig["board_ids"] = [b.strip().upper() for b in rig["board_ids"].replace(";", ",").split(",") if b.strip()]
        for b in rig["board_ids"]:
            if len(b) not in (4, 8) or any(c not in "0123456789ABCDEF" for c in b):  # 4 = batch 1 board
                raise ValueError(f"board ID {b!r} is not 4 or 8 hex digits")
        rig["ai_fs"] = int(rig["ai_fs"])
        rig["use_daq"] = self.use_daq.get()
        rig["output_dir"] = rig["output_dir"] or os.getcwd()
        return rig

    def olfactometer(self, rig):
        """(Re)open the serial port only when the port or board list changed."""
        key = (rig["serial_port"], tuple(rig["board_ids"]))
        if self.olf is None or self.olf_key != key:
            if self.olf:
                self.olf.close()
            self.olf = (DryRunOlfactometer(rig["board_ids"]) if rig["serial_port"] == DRY
                        else SerialOlfactometer(port=rig["serial_port"], board_ids=rig["board_ids"]))
            self.olf_key = key
            self.say(f"serial: {rig['serial_port']}, boards {', '.join(rig['board_ids'])}")
        return self.olf

    def pick_output(self):
        d = filedialog.askdirectory()
        if d:
            self.vars["output_dir"].set(d)

    def load_odor_list(self, txt, path=None):
        """Find (or take) the mouse's odor list for this session and check the two against each other."""
        self.odors_for = txt
        try:
            o = OdorList(txt, read_experiment_file(txt), path=path,
                         dirs=self.rig.get("odor_list_dirs") or DEFAULT_DIRS)
        except Exception as e:
            self.odors = None
            self.odor_list.set("")
            self.odor_check.configure(text=f"odor list: {e}", fg="#c00000")
            return
        self.odors = o if o.vials else None
        self.odor_list.set(o.path or "")
        ok = not o.issues
        text = f"session {o.kind or '?'};  " + ";  ".join(o.notes)
        text += "  -  list and session agree" if ok else "".join(f"\n! {i}" for i in o.issues)
        self.odor_check.configure(text=text, fg="gray" if ok else "#c00000")
        self.say(f"odor list: {os.path.basename(o.path) if o.path else 'none'}"
                 + ("" if ok else f", {len(o.issues)} problem(s): " + " | ".join(o.issues)))

    def pick_odor_list(self):
        f = filedialog.askopenfilename(filetypes=[("odor list", "odor_list_*.csv"), ("all", "*")])
        if f and self.txt.get():
            self.load_odor_list(self.txt.get(), f)

    def pick_txt(self):
        f = filedialog.askopenfilename(filetypes=[("experiment", "*.txt"), ("all", "*")])
        if f:
            self.txt.set(f)
            self.load_odor_list(f)
            rows = read_experiment_file(f)
            self.say(f"loaded {os.path.basename(f)}: {len(rows)} events, {len(rows[0]['valves'])} boards, "
                     f"{rows[-1]['time'] / 60000:.1f} min")
            if not self.run_thread:
                self.rows, self.done, self.state = rows, 0, "IDLE"
                self.t_run = self.t0 = self.frozen = None
                self.paused_s = 0.0

    # --- acquisition ---
    def toggle_acq(self):
        if self.acq:
            self.acq.stop()
            self.say(f"acquisition stopped: {self.acq.path} ({self.acq.n_samples} samples)")
            self.acq = None
            self.acq_btn.configure(text="Start acquisition")
            return
        try:
            rig = self.settings()
            from daq import Acquisition
            path = os.path.join(rig["output_dir"], stamp() + ".tdms")
            self.acq = Acquisition(rig["ai_channels"], rig["ai_fs"], path, rig["ai_terminal"])
            self.acq.start()
        except Exception as e:
            self.acq = None
            messagebox.showerror("Acquisition", str(e))
            return
        self.say(f"acquisition started: {path}")
        self.acq_btn.configure(text="Stop acquisition")

    # --- experiment ---
    def toggle_run(self):
        if self.run_thread:
            self.stop.set()
            self.say("stopping...")
            return
        try:
            rig = self.settings()
            rows = read_experiment_file(self.txt.get())
            if self.odors_for != self.txt.get():  # typed in instead of loaded
                self.load_odor_list(self.txt.get())
            if rig["use_daq"] and not self.acq and not messagebox.askyesno(
                    "No acquisition", "Acquisition is not running; nothing will be recorded. Run anyway?"):
                return
            olf = self.olfactometer(rig)
            dio = None
            if rig["use_daq"]:
                from daq import DigitalOut
                dio = DigitalOut(rig["pattern_lines"], rig["counter_lines"])
            with open(RIG, "w") as f:
                json.dump(rig, f, indent=4)
        except Exception as e:
            messagebox.showerror("Run", str(e))
            return
        self.stop.clear()
        self.pause.clear()
        self.progress.configure(maximum=len(rows), value=0)
        stem = os.path.splitext(os.path.basename(self.txt.get()))[0]
        self.rows, self.done, self.state = rows, 0, "WAITING"
        self.t_run = self.t0 = time.perf_counter()
        self.frozen, self.paused_s = None, 0.0
        self.history.append((self.t_run, "0" * 16, 0))
        now = time.perf_counter

        def work():
            try:
                paths = run_experiment(rows, olf, rig["output_dir"], stem, dio=dio, stop=self.stop,
                                       on_event=lambda i, n, r: self.events.put(("event", i, n, r, now())),
                                       pause=self.pause, on_pause=lambda p: self.events.put(("pause", p, now())))
                self.events.put(("done", "stopped" if self.stop.is_set() else "finished", paths, now()))
            except Exception as e:
                self.events.put(("done", f"ERROR: {e}", [], now()))
            finally:
                if dio:
                    dio.close()

        self.run_thread = threading.Thread(target=work, daemon=True)
        self.run_thread.start()
        self.run_btn.configure(text="Stop")
        self.pause_btn.configure(text="Pause", state="normal")
        self.say(f"run started: {stem}, {len(rows)} events")

    def toggle_pause(self):
        if not self.run_thread:
            return
        if self.pause.is_set():
            self.pause.clear()
            self.pause_btn.configure(text="Pause")
        else:
            self.pause.set()
            self.pause_btn.configure(text="Resume")

    def proto_time(self, t):
        """Protocol time (s, as in column 1 of the .txt) at perf_counter time t; it stands still while paused."""
        if self.frozen is not None:
            return self.frozen
        return t - self.t0 if self.t0 is not None else 0.0

    def handle(self, ev):
        kind, t = ev[0], ev[-1]
        if kind == "event":
            _, i, n, r, _ = ev
            self.done, self.state = i, "RUNNING"
            self.t0 = t - r["time"] / 1000  # resync the protocol clock on every row
            self.progress.configure(value=i)
            self.markers.append((t, str(i)))
            self.history.append((t, r["pattern"], (i - 1) % 128))
        elif kind == "pause":
            paused = ev[1]
            if paused:
                self.frozen, self.t_pause, self.state = self.proto_time(t), t, "PAUSED"
                last = self.history[-1] if self.history else (t, "0" * 16, 0)
                self.history.append((t, "0" * 16, last[2]))  # pattern low, counter held
            else:
                self.t0, self.frozen = t - self.frozen, None
                self.paused_s += t - self.t_pause
                self.state = "RUNNING" if self.done else "WAITING"
            self.markers.append((t, "pause" if paused else "resume"))
            self.say("paused: valves blank, pattern lines low" if paused else "resumed")
        else:  # done
            _, msg, paths, _ = ev
            if self.t_pause is not None and self.frozen is not None:
                self.paused_s += t - self.t_pause
            self.frozen = self.proto_time(t)
            self.state = "ERROR" if msg.startswith("ERROR") else "STOPPED" if msg == "stopped" else "FINISHED"
            self.history.append((t, "0" * 16, 0))
            self.say(f"run {msg}; logs: {', '.join(paths)}")
            self.run_thread = None
            self.pause.clear()
            self.run_btn.configure(text="Run")
            self.pause_btn.configure(text="Pause", state="disabled")

    def poll(self):
        while not self.events.empty():
            self.handle(self.events.get())
        now = time.perf_counter()
        if self.acq and self.acq.error:
            self.say(f"ACQUISITION ERROR: {self.acq.error}")
            self.acq.error = None
        if self.acq:
            self.acq_status.configure(text=f"{self.acq.n_samples / self.acq.fs:.0f} s recorded")
        paused_now = now - self.t_pause if self.state == "PAUSED" else 0.0
        end = now if self.run_thread else (self.history[-1][0] if self.history else now)
        self.status.show(self.state, self.rows, self.done, self.proto_time(now),
                         end - self.t_run if self.t_run else 0.0, self.paused_s + paused_now,
                         self.odors)
        self.scope.draw_analog(now, self.markers, self.acq)
        p_names = line_names(self.vars["pattern_lines"].get())
        c_names = line_names(self.vars["counter_lines"].get())
        pattern, counter = self.history[-1][1:] if self.history else ("0" * 16, 0)
        self.scope.draw_digital(pattern, counter,
                                p_names if p_names and len(p_names) == 16 else [f"L{k}" for k in range(16)],
                                c_names if c_names and len(c_names) == 7 else [f"L{k}" for k in range(7)])
        self.after(150, self.poll)

    # --- manual ---
    def manual(self):
        if self.run_thread:
            return messagebox.showwarning("Manual", "Experiment running.")
        try:
            self.olfactometer(self.settings()).drive(self.m_board.get(), self.m_valve.get())
            self.say(f"drive board {self.m_board.get()} valve {self.m_valve.get()}")
        except Exception as e:
            messagebox.showerror("Manual", str(e))

    def blank(self):
        if self.run_thread:
            return messagebox.showwarning("Manual", "Experiment running.")
        try:
            rig = self.settings()
            all_blank(self.olfactometer(rig), len(rig["board_ids"]))
            self.say("all boards -> blank")
        except Exception as e:
            messagebox.showerror("Manual", str(e))

    def on_close(self):
        if self.run_thread and not messagebox.askyesno("Quit", "Experiment running. Stop it and quit?"):
            return
        self.stop.set()
        if self.run_thread:
            self.run_thread.join(timeout=5)  # run_experiment sets valves to blank on the way out
        if self.acq:
            self.acq.stop()
        if self.olf:
            self.olf.close()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
