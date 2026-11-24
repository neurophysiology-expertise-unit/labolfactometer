"""Write an experiment .txt that opens every odor valve once, in the format of matlab/create_exp_matlab.m.

Rows alternate: odor valve open, then all blank, `step` ms apart, so every 2nd event is an odor.
With the defaults (boards 1-5, valves 2-8, 2 s) that is 35 valves in 2 min 20 s.

Pattern bits, as in the MATLAB files: characters 1-7 are 1001001 on odor rows and 1000001 on blank rows,
characters 8-15 the odor ID 7*(board-1) + valve - 1, character 16 (thermal cam in create_exp_matlab.m) 0.
The last row is all zeros.

    python make_valve_check.py                       -> valve_check.txt
    python make_valve_check.py --boards 1 2 -o b12.txt
"""
import argparse

HEADER = "Delta T [ms]\t16-bit pattern\tBoard ID,Valve ID;... "
ODOR, BLANK, END = "1001001", "1000001", "0" * 16


def row(t, pattern, valves, n_boards):
    """valves: {board: valve}; every other board stays on valve 1."""
    bv = ";".join(f"{b},{valves.get(b, 1)}" for b in range(1, n_boards + 1))
    return f"{t}\t{pattern}\t{bv}\t"


def valve_check(boards, valves, step, start, n_boards):
    t = start
    lines = [HEADER, row(t, BLANK + "0" * 9, {}, n_boards)]
    for b in boards:
        for v in valves:
            t += step
            lines.append(row(t, ODOR + f"{7 * (b - 1) + v - 1:08b}" + "0", {b: v}, n_boards))
            t += step
            lines.append(row(t, BLANK + "0" * 9, {}, n_boards))
    lines.append(row(t + step, END, {}, n_boards))
    return "\n".join(lines) + "\n"


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--boards", type=int, nargs="+", default=[1, 2, 3, 4, 5])
    p.add_argument("--valves", type=int, nargs="+", default=list(range(2, 9)), help="odor valves (1 is the blank)")
    p.add_argument("--step", type=int, default=2000, help="ms between events")
    p.add_argument("--start", type=int, default=5000, help="ms of the first (blank) row")
    p.add_argument("--n-boards", type=int, default=5, help="boards listed in column 3")
    p.add_argument("-o", "--output", default="valve_check.txt")
    a = p.parse_args()
    with open(a.output, "w", newline="\n") as f:
        f.write(valve_check(a.boards, a.valves, a.step, a.start, a.n_boards))
    print(f"{a.output}: {len(a.boards) * len(a.valves)} valves, {a.step} ms apart")


if __name__ == "__main__":
    main()
