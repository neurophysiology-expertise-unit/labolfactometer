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
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from serial.tools import list_ports

from experiment_runner import (DryRunOlfactometer, SerialOlfactometer, all_blank,
                               run_experiment, stamp)
from io_utils import read_experiment_file

RIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rig.json")
DRY = "dry-run (no hardware)"
FIELDS = [("serial_port", "Serial port"), ("board_ids", "Board IDs (board 1, 2, ...)"),
          ("ai_channels", "AI channels"), ("ai_fs", "AI rate [Hz]"), ("ai_terminal", "AI terminal"),
          ("pattern_lines", "16-bit pattern DO"), ("counter_lines", "7-bit counter DO"),
          ("output_dir", "Output folder")]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Olfactometer")
        with open(RIG) as f:
            self.rig = json.load(f)
        self.olf = self.acq = self.run_thread = None
        self.stop = threading.Event()
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
        self.progress = ttk.Progressbar(exp, length=300)
        self.progress.grid(row=1, column=1, sticky="ew")
        self.exp_status = ttk.Label(exp, text="")
        self.exp_status.grid(row=2, column=0, columnspan=3, sticky="w")

        man = ttk.LabelFrame(self, text="Manual valve (flush / test)")
        man.grid(row=3, column=0, sticky="ew", padx=6)
        self.m_board, self.m_valve = tk.IntVar(value=1), tk.IntVar(value=1)
        ttk.Label(man, text="board").grid(row=0, column=0)
        ttk.Spinbox(man, from_=1, to=16, width=4, textvariable=self.m_board).grid(row=0, column=1)
        ttk.Label(man, text="valve").grid(row=0, column=2)
        ttk.Spinbox(man, from_=1, to=8, width=4, textvariable=self.m_valve).grid(row=0, column=3)
        ttk.Button(man, text="Drive", command=self.manual).grid(row=0, column=4)
        ttk.Button(man, text="All blank", command=self.blank).grid(row=0, column=5)

        self.log = tk.Text(self, height=12, width=80, state="disabled")
        self.log.grid(row=4, column=0, sticky="nsew", padx=6, pady=6)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(200, self.poll)

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
            if len(b) != 8 or any(c not in "0123456789ABCDEF" for c in b):
                raise ValueError(f"board ID {b!r} is not 8 hex digits")
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

    def pick_txt(self):
        f = filedialog.askopenfilename(filetypes=[("experiment", "*.txt"), ("all", "*")])
        if f:
            self.txt.set(f)
            rows = read_experiment_file(f)
            self.say(f"loaded {os.path.basename(f)}: {len(rows)} events, {len(rows[0]['valves'])} boards, "
                     f"{rows[-1]['time'] / 60000:.1f} min")

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
        self.progress.configure(maximum=len(rows), value=0)
        stem = os.path.splitext(os.path.basename(self.txt.get()))[0]

        def work():
            try:
                paths = run_experiment(rows, olf, rig["output_dir"], stem, dio=dio, stop=self.stop,
                                       on_event=lambda i, n, r: self.events.put(("event", i, n, r)))
                self.events.put(("done", "stopped" if self.stop.is_set() else "finished", paths))
            except Exception as e:
                self.events.put(("done", f"ERROR: {e}", []))
            finally:
                if dio:
                    dio.close()

        self.run_thread = threading.Thread(target=work, daemon=True)
        self.run_thread.start()
        self.run_btn.configure(text="Stop")
        self.say(f"run started: {stem}, {len(rows)} events")

    def poll(self):
        while not self.events.empty():
            ev = self.events.get()
            if ev[0] == "event":
                _, i, n, r = ev
                self.progress.configure(value=i)
                self.exp_status.configure(text=f"event {i}/{n}  t={r['time'] / 1000:.1f} s  "
                                               f"pattern {r['pattern']}  valves {r['valve_id']}")
            else:
                self.say(f"run {ev[1]}; logs: {', '.join(ev[2])}")
                self.run_thread = None
                self.run_btn.configure(text="Run")
        if self.acq:
            if self.acq.error:
                self.say(f"ACQUISITION ERROR: {self.acq.error}")
                self.acq.error = None
            last = "  ".join(f"{v:.2f}" for v in self.acq.last[:8])
            self.acq_status.configure(text=f"{self.acq.n_samples / self.acq.fs:.0f} s recorded   AI0-7 [V]: {last}")
        self.after(200, self.poll)

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
