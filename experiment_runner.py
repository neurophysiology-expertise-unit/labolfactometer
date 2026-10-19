"""Play an experiment .txt (from matlab/create_exp_matlab.m) on the olfactometer.

Replaces what the LabVIEW VI did at every row of the .txt:
  - drive one valve on every solenodrive board over RS485 (column 3, "board,valve;...")
  - write the 16-bit pattern to static DO lines (column 2)          -> *_NonTimedDO.txt
  - write the 7-bit event counter (row-1) % 128 to DO lines          -> *_TimedDO.txt
Column 1 is the time of the row in ms from the start of the run (not a delta).

CLI:  python experiment_runner.py Day1_XT020.txt [rig.json]
GUI:  python olfactometer_gui.py
"""
import json
import os
import sys
import time
import threading
import warnings
from datetime import datetime

import serial

from io_utils import read_experiment_file

# --- Constants ---
SOF = 0x0F
EOF = 0x04
DLE = 0x05
DRIVE = 0x44
BLANK_VALVE = 1  # valve 1 of every board is the blank (clean air) line
COUNTER_START = 0b1111111  # LabVIEW writes all-ones before the first event


# --- Serial Communication Functions ---
def command_string(board_id, valve, command=DRIVE):
    """Generates command bytes for a given valve on a given PCB board

    >>> [hex(x) for x in command_string("04049F01", 4, DRIVE)]
    ['0xf', '0x5', '0x4', '0x5', '0x4', '0x9f', '0x1', '0x44', '0x34', '0x0', '0x0', '0x4']

    The valve goes out as ASCII ('4' = 0x34). The original example expected a raw 0x04;
    the code has always added 0x30, so check on the rig which one the solenodrive takes.

    :param str board_id: Address of the board as a string of 4 bytes (e.g. '0A05F2DE')
    :param int valve: valve on the PCB board to send command to (1..8)
    :param int command: command to send to the channel: 0x44 = drive channel, 0x45: Enable relais driver, 0x49: Idle relais driver
    :returns bytearray of at least 10 bytes containing the command to be relayed to the solenodrive
    """
    assert command in (0x44, 0x45, 0x49), "Command byte not recognised, must be 0x44, 0x45 or 0x49"

    valve += 0x30
    board_array = [int(board_id[i:i+2], 16) for i in range(0, len(board_id), 2)]
    command_array = board_array + [command, valve]
    ind = 0
    while ind < len(command_array):
        # Add escape bytes (0x05) before any special characters,
        #   this way, they will be interpreted as data by the solenodrive
        if command_array[ind] in (SOF, EOF, DLE):
            command_array.insert(ind, 5)
            ind += 1
        ind += 1
    # Currently, the CRC checksum is ignored by the solenodrive firmware, so we just send 2 zeros instead
    #   With updates of the firmware, it might become necessary to add the correct CRC checksum
    return bytearray([SOF] + command_array + [0, 0] + [EOF])


class SerialOlfactometer(serial.Serial):
    """Solenodrive interface for triggering olfactometer
        This class can be used to interact with the solenodrive PCBs using the RS485 communication standard
    """

    def __init__(self, port="/dev/ttyUSB0", board_ids=(), baudrate=57600, bytesize=serial.EIGHTBITS, timeout=None, **kwargs):
        """Create connection to the solenodrive"""
        if baudrate != 57600:
            warnings.warn("Baudrate used by solenodrive is 57600")
        super().__init__(port=port, baudrate=baudrate, bytesize=bytesize, timeout=timeout, **kwargs)
        self.board_ids = list(board_ids)

    def drive(self, board, valve):
        """Drive `valve` (1..8) on `board` (1-based index into board_ids, as in the .txt)."""
        self.write(command_string(self.board_ids[board - 1], valve))


class DryRunOlfactometer:
    """Stands in for SerialOlfactometer without hardware: records what would be sent."""

    def __init__(self, board_ids=()):
        self.board_ids = list(board_ids)
        self.sent = []

    def drive(self, board, valve):
        command_string(self.board_ids[board - 1], valve)  # same IndexError as the real one
        self.sent.append((board, valve))

    def close(self):
        pass


def stamp(dt=None):
    """LabVIEW timestamp format, e.g. 11062026_115137,359."""
    dt = dt or datetime.now()
    return dt.strftime("%d%m%Y_%H%M%S,") + f"{dt.microsecond // 1000:03d}"


def wait_until(t, stop):
    """Sleep until perf_counter() >= t; return False if `stop` was set first."""
    while True:
        left = t - time.perf_counter()
        if stop.is_set():
            return False
        if left <= 0:
            return True
        # ponytail: sleep + 2 ms spin gives ~1 ms jitter; hardware-timed DO if that is ever not enough
        if left > 0.002:
            time.sleep(min(left - 0.002, 0.1))


def all_blank(olf, n_boards):
    for b in range(1, n_boards + 1):
        olf.drive(b, BLANK_VALVE)


def run_experiment(rows, olf, log_dir, log_stem, dio=None, stop=None, on_event=None):
    """Play `rows` (io_utils.read_experiment_file) in real time.

    olf      : SerialOlfactometer or DryRunOlfactometer
    dio      : daq.DigitalOut or None (no NI board: patterns are only logged)
    stop     : threading.Event to abort; valves go back to blank and DO to 0 either way
    on_event : callback(i, n, row) after each row, for the GUI
    Returns the paths of the two DO logs.
    """
    stop = stop or threading.Event()
    n_boards = max(len(r["valves"]) for r in rows)
    if n_boards > len(olf.board_ids):
        raise ValueError(f"the .txt drives {n_boards} boards but only {len(olf.board_ids)} board IDs are set")
    for r in rows:
        if len(r["valves"]) != n_boards or [b for b, _ in r["valves"]] != list(range(1, n_boards + 1)):
            raise ValueError(f"row at {r['time']} ms has valves {r['valve_id']!r}; expected boards 1..{n_boards} in order")

    start = stamp()
    paths = [os.path.join(log_dir, f"{log_stem}_{start}_{kind}.txt") for kind in ("NonTimedDO", "TimedDO")]
    with open(paths[0], "w") as nontimed, open(paths[1], "w") as timed:
        all_blank(olf, n_boards)
        if dio:
            dio.write(rows[0]["pattern"], COUNTER_START)
        nontimed.write(f"0\t{rows[0]['pattern']}\t0\t{start}\n")
        timed.write(f"0\t{COUNTER_START:07b}\t0\t{start}\n")
        t0 = time.perf_counter()
        try:
            for i, row in enumerate(rows, 1):
                if not wait_until(t0 + row["time"] / 1000, stop):
                    break
                for board, valve in row["valves"]:
                    olf.drive(board, valve)
                counter = (i - 1) % 128
                if dio:
                    dio.write(row["pattern"], counter)
                ms = round((time.perf_counter() - t0) * 1000)
                nontimed.write(f"{i}\t{row['pattern']}\t{ms}\t{stamp()}\n")
                timed.write(f"{i}\t{counter:07b}\t{ms}\t{stamp()}\n")
                if on_event:
                    on_event(i, len(rows), row)
        finally:
            all_blank(olf, n_boards)
            if dio:
                dio.write("0" * 16, 0)
    return paths


def load_rig(path):
    with open(path) as f:
        return json.load(f)


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: python experiment_runner.py experiment.txt [rig.json]")
    rig = load_rig(sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "rig.json"))
    rows = read_experiment_file(sys.argv[1])
    olf = SerialOlfactometer(port=rig["serial_port"], board_ids=rig["board_ids"])
    dio = None
    if rig.get("use_daq"):
        from daq import DigitalOut
        dio = DigitalOut(rig["pattern_lines"], rig["counter_lines"])
    stem = os.path.splitext(os.path.basename(sys.argv[1]))[0]
    try:
        paths = run_experiment(rows, olf, rig.get("output_dir") or ".", stem, dio=dio,
                               on_event=lambda i, n, r: print(f"{i}/{n}  {r['time']} ms  {r['pattern']}  {r['valve_id']}"))
    except KeyboardInterrupt:
        paths = []
    finally:
        olf.close()
        if dio:
            dio.close()
    print("logs:", *paths)


if __name__ == "__main__":
    main()
