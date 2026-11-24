# labolfactometer

Python replacement for the LabVIEW olfactometer VI (Haesler lab rig).

```
matlab/create_exp_matlab.m  ->  Experiment .txt  ->  olfactometer_gui.py  ->  RS485 (USB converter)  ->  solenodrive boards  ->  odor valves
                                                                         ->  NI-DAQ DO (16-bit pattern + 7-bit event counter)
                                                     NI-DAQ AI (16 ch, 1 kHz)  ->  <stamp>.tdms
```

## Run

```
micromamba env create -f environment.yml
micromamba activate lab
python olfactometer_gui.py
```

1. Check the **Rig** fields (saved to `rig.json` on every Run).
2. **Start acquisition**: writes `<output>/<ddmmYYYY_HHMMSS,mmm>.tdms`, same layout as the LabVIEW files
   (group `Measured Data`, channels `AI0`..`AI15`) and its `.tdms_index` (headers only, for faster opening;
   readers work without it, but copy both files or the `.tdms` alone, never a stale index). The **Signals** panel shows the last 2-60 s on one time axis:
   every AI channel (same scale by default), with events (red) and pauses (orange). Below it, one dot per DO line
   lights while that line is set high (facecam, odor ID, other pattern lines, counter). **Experiment state** shows the run state, protocol and elapsed
   time, the current row and open valves, and the next rows with countdowns.
3. **Load .txt** (made by `matlab/create_exp_matlab.m`), then **Run**. At each row the GUI drives one valve on
   every board, writes the 16-bit pattern and the event counter `(row-1) % 128` to DO, and logs
   `<txt>_<stamp>_NonTimedDO.txt` / `_TimedDO.txt` in the LabVIEW format.
   DO stays at 0 until the first row's time: only the rows of the .txt set lines high.
   **Novel / familiar / blank**: loading a .txt finds the mouse's `odor_list_<mouse>week<n>.csv` (next to the .txt,
   one folder up, or under `odor_list_dirs` in rig.json, default `Desktop\experiment_files` and `Desktop`) and picks
   the one that fits the vials the session presents; **...** picks another one. The `valve` column is the vial = odor
   ID in the .txt. **Experiment state** shows the odor that is on, or the next one, with its chemical, as novel
   (magenta), familiar (teal) or blank (slate), and the next-events list labels each odor. The list is checked
   against the session and shown in red when they disagree: presented vials the list doesn't label, copies of the
   list that differ, a vial marked both novel and familiar, and labels that disagree with the mouse's earlier
   `Familiar` sessions (`.mat` files in folders `<yymmdd>_<mouse>` next to the .txt's folder). See `novelty.py`.
4. **Pause** puts every board on valve 1 (blank) and the pattern lines low (the counter keeps its value) and stops
   the clock. **Resume** runs the next row after the time it still had left; all later rows shift by the pause.
   Pauses are logged to `<txt>_<stamp>_Pauses.txt` and marked orange in the traces.
5. **Stop** (or an error, or closing the window, also while paused) puts every board back on valve 1 (blank) and DO to 0.

The **Manual valve** box drives one valve by hand, for flushing and testing. Pick `dry-run (no hardware)` as the
serial port to run a file without the rig.

Without the GUI: `python experiment_runner.py Day1_XT020.txt [rig.json]` (valves + DO only, no acquisition).

## The .txt

```
Delta T [ms]    16-bit pattern      Board ID,Valve ID;...
20548           1000001000000001    1,1;2,1;3,1;4,1;5,1
24548           1001001000111010    1,1;2,1;3,1;4,1;5,2
```

- Column 1 is the time from the start of the run in ms, not a delta.
- Column 2 is the DO pattern, written in reading order: character *k* goes to the *k*-th line of `pattern_lines`
  (character 1 -> port1/line0, the facecam; it stays high until the trial's last row). Characters 8-15 are the
  odor ID. `create_exp_matlab.m` calls character 16 the thermal cam; it is high on the trial-start row only.
- Column 3 gives the valve for each board in board order. Valve 1 is the blank; valves 2-8 carry odors (7 per board).
- Board *n* in the file is the *n*-th entry of `board_ids`.

## rig.json: check against the LabVIEW VI before the first real session

| key | default | note |
|---|---|---|
| `serial_port` | `COM7` | FTDI USB-RS485 converter, 57600 baud; same as `COM port RS-485` in LabVIEW's `Dev_Env/Settings/Settings_General.ini` |
| `board_ids` | `1B25, 1A3A, 1901600C, 1901C01B, 0D02B01D` | as written on the boards and in the LabVIEW ini, in board order. 2-byte IDs are batch 1 boards (older firmware), sent with an extra `05 05` before the address |
| `ai_channels`, `ai_fs`, `ai_terminal` | `Dev1/ai0:15`, 1000, `RSE` | channels and rate match the LabVIEW ini and TDMS; the terminal mode is a guess |
| `pattern_lines` | `Dev1/port1/line0:7,Dev1/port2/line0:7` | LabVIEW's non-timed DO ports (the PCIe-6351 has 8 lines per port); the *k*-th line listed gets character *k* of the pattern |
| `counter_lines` | `Dev1/port0/line0:6` | LabVIEW's timed DO port |

Checked on the rig (29-10-2026): the solenodrive takes the valve number as ASCII (`'2'` = 0x32), not a raw 0x02,
and switches without "enable relay driver" (0x45). Batch 1 boards take `0F 05 05 05 05 <2-byte id> 44 <valve> 00 00 04`,
the frame LabVIEW sends (NI IO Trace).

Open questions for the rig:
- The DO lines are written by software (about 1 ms jitter). The TimedDO counter in LabVIEW may have been
  hardware-timed.

## Check

`python test_experiment_runner.py` replays rows of 260611_XT020 `Day1_XT020.txt` against the logs LabVIEW wrote for
that session. To compare the whole session:
`python test_experiment_runner.py Day1_XT020.txt <...>_NonTimedDO.txt <...>_TimedDO.txt`.

## Known problems in `experiment_generator.py` (Python port of the MATLAB generator)

Do not use it for sessions yet. Use `matlab/create_exp_matlab.m`.
- Odor 1 is mapped to `1,1`, which is the blank valve. MATLAB maps odor *i* to valve `i - 7*(board-1) + 1`.
- The valve opens on the trial-start row, not on the odor-on row, so odor flows for 6 s instead of 2 s.
- The ITIs come from a truncated normal. MATLAB uses a truncated exponential.
