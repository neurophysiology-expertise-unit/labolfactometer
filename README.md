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
   (group `Measured Data`, channels `AI0`..`AI15`).
3. **Load .txt** (made by `matlab/create_exp_matlab.m`), then **Run**. At each row the GUI drives one valve on
   every board, writes the 16-bit pattern and the event counter `(row-1) % 128` to DO, and logs
   `<txt>_<stamp>_NonTimedDO.txt` / `_TimedDO.txt` in the LabVIEW format.
4. **Stop** (or an error, or closing the window) puts every board back on valve 1 (blank) and DO to 0.

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
- Column 2 is the DO pattern. The last character is bit 0 (thermal cam), and characters 8-15 are the odor ID.
- Column 3 gives the valve for each board in board order. Valve 1 is the blank; valves 2-8 carry odors (7 per board).
- Board *n* in the file is the *n*-th entry of `board_ids`.

## rig.json: check against the LabVIEW VI before the first real session

| key | default | note |
|---|---|---|
| `serial_port` | `COM3` | USB-RS485 converter, 57600 baud |
| `board_ids` | `0D02B017, 0D02B016, 0D02B015, 0D02B01E, 0D02C019` | from `experiment_runner.py` upstream; the older script had `0D02C01E` as board 1 |
| `ai_channels`, `ai_fs`, `ai_terminal` | `Dev1/ai0:15`, 1000, `RSE` | channels and rate taken from the 260611_XT020 TDMS; the terminal mode is a guess |
| `pattern_lines` | `Dev1/port0/line0:15` | **guess**; line *k* gets bit *k*. Write `line15:0` to reverse the order |
| `counter_lines` | `Dev1/port1/line0:6` | **guess** |

Open questions for the rig:
- `command_string` sends the valve number as ASCII (`'2'` = 0x32). The original docstring expected a raw 0x02.
  Drive board 1 / valve 2 by hand and listen for the click.
- LabVIEW may have sent "enable relay driver" (0x45) before the session. This code sends only DRIVE (0x44).
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
