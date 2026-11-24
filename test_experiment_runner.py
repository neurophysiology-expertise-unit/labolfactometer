"""python test_experiment_runner.py [experiment.txt LabVIEW_NonTimedDO.txt LabVIEW_TimedDO.txt]

Without arguments: first 8 rows of 260611_XT020/Day1_XT020.txt against what LabVIEW logged for them.
With arguments: replays the whole file 100x faster and compares the full LabVIEW logs.
"""
import doctest
import os
import sys
import tempfile

import experiment_runner
from experiment_runner import DryRunOlfactometer, run_experiment
from io_utils import read_experiment_file

TXT = """Delta T [ms]\t16-bit pattern\tBoard ID,Valve ID;...
20548\t1000001000000001\t1,1;2,1;3,1;4,1;5,1\t
24548\t1001001000111010\t1,1;2,1;3,1;4,1;5,2\t
26548\t1000001000000000\t1,1;2,1;3,1;4,1;5,1\t
32548\t0000000000000000\t1,1;2,1;3,1;4,1;5,1\t
50227\t1000001000000001\t1,1;2,1;3,1;4,1;5,1\t
54227\t1001001000111010\t1,1;2,1;3,1;4,1;5,2\t
56227\t1000001000000000\t1,1;2,1;3,1;4,1;5,1\t
62227\t0000000000000000\t1,1;2,1;3,1;4,1;5,1\t
"""
# columns 0-1 of LabVIEW's Day1_XT020_11062026_115137,359_{NonTimed,Timed}DO.txt, rows 0-8
LV_NONTIMED = ["0\t1000001000000001"] + [f"{i}\t{p}" for i, p in enumerate(
    ["1000001000000001", "1001001000111010", "1000001000000000", "0000000000000000"] * 2, 1)]
LV_TIMED = ["0\t1111111"] + [f"{i}\t{i - 1:07b}" for i in range(1, 9)]


class FakeDIO:
    def __init__(self):
        self.written = []

    def write(self, pattern, counter):
        self.written.append((pattern, counter))


def first_two_columns(path):
    with open(path) as f:
        return ["\t".join(line.split("\t")[:2]) for line in f]


def replay(txt_path, speedup):
    rows = read_experiment_file(txt_path)
    for r in rows:
        r["time"] /= speedup
    olf, dio = DryRunOlfactometer(["0D02B017", "0D02B016", "0D02B015", "0D02B01E", "0D02C019"]), FakeDIO()
    out = tempfile.mkdtemp()
    paths = run_experiment(rows, olf, out, "test", dio=dio)
    return rows, olf, dio, paths


def check_pause():
    """Pause 300 ms between rows 1 and 2: valves blank, pattern lines low, counter held, later rows shifted."""
    import threading
    import time
    rows = [{"time": t, "pattern": p, "valve_id": "", "valves": [(1, v), (2, 1)]}
            for t, p, v in ((100, "1" * 16, 2), (200, "0" * 15 + "1", 3), (300, "0" * 16, 1))]
    olf, dio, pause = DryRunOlfactometer(["1B25", "1A3A"]), FakeDIO(), threading.Event()
    times, states = [], []
    threading.Timer(0.15, pause.set).start()
    threading.Timer(0.45, pause.clear).start()
    t0 = time.perf_counter()
    out = tempfile.mkdtemp()
    run_experiment(rows, olf, out, "p", dio=dio, pause=pause,
                   on_event=lambda i, n, r: times.append(time.perf_counter() - t0),
                   on_pause=lambda p: states.append((p, list(olf.sent[-2:]), dio.written[-1], time.perf_counter())))
    held = states[1][3] - states[0][3]  # actual pause; Timer can fire late on Windows
    assert abs(times[0] - 0.1) < 0.02 and abs(times[1] - 0.2 - held) < 0.02 and abs(times[2] - 0.3 - held) < 0.02, \
        (times, held)
    assert states[0][:3] == (True, [(1, 1), (2, 1)], ("0" * 16, 0)), states  # blank, pattern low, counter of row 1
    log = [f for f in os.listdir(out) if f.endswith("_Pauses.txt")]
    assert len(log) == 1 and open(os.path.join(out, log[0])).read().startswith("pause before row 2")


if __name__ == "__main__":
    check_pause()
    assert doctest.testmod(experiment_runner).failed == 0
    if len(sys.argv) == 4:
        txt, lv_nontimed, lv_timed = sys.argv[1:]
        speedup = 100
    else:
        txt = os.path.join(tempfile.mkdtemp(), "Day1_XT020.txt")
        with open(txt, "w") as f:
            f.write(TXT)
        lv_nontimed = lv_timed = None
        speedup = 1000
    rows, olf, dio, (nontimed, timed) = replay(txt, speedup)

    expect_nt = first_two_columns(lv_nontimed) if lv_nontimed else LV_NONTIMED
    expect_t = first_two_columns(lv_timed) if lv_timed else LV_TIMED
    assert first_two_columns(nontimed) == expect_nt, "NonTimedDO differs from LabVIEW"
    assert first_two_columns(timed) == expect_t, "TimedDO differs from LabVIEW"

    n_boards = len(rows[0]["valves"])
    blank = [(b, 1) for b in range(1, n_boards + 1)]
    assert olf.sent[:n_boards] == blank and olf.sent[-n_boards:] == blank, "must start and end on blank"
    assert olf.sent[n_boards:-n_boards] == [bv for r in rows for bv in r["valves"]], "every row drives every board"
    assert dio.written[0] == ("0" * 16, 0), "DO must stay at 0 until the first row"
    assert dio.written[-1] == ("0" * 16, 0), "DO must end at 0"
    assert [p for p, _ in dio.written[1:-1]] == [r["pattern"] for r in rows]
    print(f"ok: {len(rows)} events, {n_boards} boards, logs match LabVIEW")
