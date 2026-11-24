"""Novel, familiar or blank per odor, from the generator's odor lists, checked against the session file.

The generator (experiment_file_generator_counterbalanced.py) writes per mouse `odor_list_<mouse>week<n>.csv`:

    chemical,id,valve,novelty,familiarity,blank,left_right
    eugenol,108,4,0,1,0,0          <- vial 4 is familiar this week
    P.E.A,120,14,1,0,0,0           <- vial 14 is novel
    mineral oil,117,15,0,0,1,0     <- vial 15 is a mineral-oil blank

`valve` is the vial number, the odor ID in the .txt: vial i sits on board ceil(i/7), valve i - 7*(board-1) + 1
(physical valve 1 of every board is the empty line; vials 1, 8, 15, 22, 29 hold mineral oil).

For a session the GUI picks the list of the mouse that fits the vials the .txt presents, and reports what does
not add up: vials the list doesn't label, listed odors the session never presents, copies of the same list that
differ, a vial marked both novel and familiar, and odors whose label disagrees with the mouse's earlier sessions
(.mat files with Novelty_task_char 'Familiar', in folders <yymmdd>_<mouse> next to the session's folder).
"""
import csv
import glob
import os
import re
from dataclasses import dataclass

FOLDER = re.compile(r"^(\d{6})_(.+)$")  # yymmdd_mouse
DEFAULT_DIRS = [os.path.expanduser(r"~\Desktop\experiment_files"), os.path.expanduser(r"~\Desktop")]


@dataclass
class Vial:
    chemical: str
    novel: bool
    familiar: bool
    blank: bool

    @property
    def role(self):
        if self.blank:
            return "blank"
        if self.novel and self.familiar:
            return "novel+familiar?"
        return "novel" if self.novel else "familiar" if self.familiar else "not used"


def odor_ids(row):
    """Odor IDs (vials) a row opens: valve v on board b is vial 7*(b-1) + v - 1 (valve 1 is the empty line)."""
    return [7 * (b - 1) + v - 1 for b, v in row["valves"] if v != 1]


def session_vials(rows):
    return {i for r in rows for i in odor_ids(r)}


def read_odor_list(path):
    """{vial: Vial} from an odor_list csv."""
    vials = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if not (r.get("valve") or "").strip():
                continue
            flag = lambda k: (r.get(k) or "0").strip() == "1"  # noqa: E731
            vials[int(r["valve"])] = Vial(r["chemical"].strip(), flag("novelty"), flag("familiarity"), flag("blank"))
    return vials


def mouse_of(txt):
    """'XT046' from .../261111_XT046/Day5_XT046.txt (folder name first, then the end of the file name)."""
    m = FOLDER.match(os.path.basename(os.path.dirname(os.path.abspath(txt))))
    if m:
        return m[2]
    m = re.search(r"_([A-Za-z]+\d+)$", os.path.splitext(os.path.basename(txt))[0])
    return m[1] if m else None


def find_odor_lists(txt, dirs=DEFAULT_DIRS, depth=3):
    """Every odor_list_<mouse>week*.csv for the mouse of this .txt: next to it, one folder up, and in `dirs`."""
    mouse = mouse_of(txt)
    if not mouse:
        return []
    here = os.path.dirname(os.path.abspath(txt))
    found = {}  # normcase path -> path as written on disk
    for root in [here, os.path.dirname(here)] + list(dirs):
        for d in range(depth + 1):
            pattern = os.path.join(root, *["*"] * d, f"odor_list_{mouse}week*.csv")
            for p in glob.glob(pattern):
                found.setdefault(os.path.normcase(os.path.abspath(p)), os.path.abspath(p))
    return sorted(found.values())


def read_mat(path):
    """(session type, vials presented) from a generator .mat; (None, []) if unreadable."""
    try:
        from scipy.io import loadmat
        m = loadmat(path, squeeze_me=True, struct_as_record=False)
    except Exception:
        return None, []
    kind = m.get("Novelty_task_char")
    kind = str(kind) if isinstance(kind, str) and kind else None
    try:
        ids = [int(i) for i in getattr(m["trial"], "odor_ID").ravel() if int(i)]
    except Exception:
        ids = []
    return kind, ids


def session_type(txt):
    """'Familiar' / 'Novel' / ... from the .mat with the same name as the .txt, or None."""
    return read_mat(os.path.splitext(txt)[0] + ".mat")[0]


def earlier_familiar(txt):
    """(vials of the mouse's earlier 'Familiar' sessions, their .mat files); (None, []) if none found."""
    folder = os.path.dirname(os.path.abspath(txt))
    m = FOLDER.match(os.path.basename(folder))
    if not m:
        return None, []
    date, mouse = m.groups()
    ids, sources = set(), []
    for d in sorted(glob.glob(os.path.join(os.path.dirname(folder), f"*_{mouse}"))):
        dm = FOLDER.match(os.path.basename(d))
        if not dm or dm[2] != mouse or dm[1] >= date:
            continue
        for mat in sorted(glob.glob(os.path.join(d, "*.mat"))):
            k, presented = read_mat(mat)
            if k == "Familiar" and presented:
                ids |= set(presented)
                sources.append(f"{os.path.basename(d)}/{os.path.basename(mat)}")
    return (ids, sources) if sources else (None, [])


def fit(vials, presented):
    """How well a list fits the vials a session presents; lower is better.

    0: its novel + familiar odors are exactly the session's odors (a novelty day and its test day),
    1: every odor of the session is familiar in it (a familiarization day),
    2 + n: n of the session's vials have no label in it.
    """
    odors = {i for i in presented if not (i in vials and vials[i].blank)}
    labelled = {i for i, v in vials.items() if v.novel or v.familiar}
    if odors == labelled:
        return 0
    if all(i in vials and vials[i].familiar and not vials[i].novel for i in odors):
        return 1
    return 2 + len([i for i in presented if i not in vials or vials[i].role == "not used"])


def week_of(path):
    m = re.search(r"week(\d+)", os.path.basename(path))
    return int(m[1]) if m else 0


class OdorList:
    """The odor list chosen for one session, with the findings of the check."""

    def __init__(self, txt, rows, path=None, dirs=DEFAULT_DIRS):
        self.txt, self.presented = txt, session_vials(rows)
        self.kind = session_type(txt)
        self.issues, self.notes = [], []
        if path:
            candidates = [path]
        else:
            candidates = find_odor_lists(txt, dirs)
        self.path, self.vials = None, None
        if not candidates:
            self.issues.append(f"no odor_list_{mouse_of(txt)}week*.csv found")
            return
        lists = {p: read_odor_list(p) for p in candidates}
        best = min(lists, key=lambda p: (fit(lists[p], self.presented), week_of(p), -os.path.getmtime(p)))
        self.path, self.vials = best, lists[best]
        score = fit(self.vials, self.presented)
        self.notes.append(f"{os.path.basename(best)} ({'exact match' if score == 0 else 'all familiar' if score == 1 else 'partial fit'})")
        # copies of the same list that differ
        for p, v in lists.items():
            if p != best and os.path.basename(p).lower() == os.path.basename(best).lower() and v != self.vials:
                diff = sorted(i for i in set(v) | set(self.vials) if v.get(i) != self.vials.get(i))
                self.issues.append(f"another copy differs (vials {', '.join(map(str, diff))}): {p}")
        self.check()

    def check(self):
        v, presented = self.vials, self.presented
        for i in sorted(presented):
            if i not in v:
                self.issues.append(f"vial {i} is presented but not in the list")
            elif v[i].role in ("not used", "novel+familiar?"):
                self.issues.append(f"vial {i} ({v[i].chemical}) is presented but marked {v[i].role!r}")
        for i, x in sorted(v.items()):
            if x.novel and x.familiar and i not in presented:
                self.issues.append(f"vial {i} ({x.chemical}) is marked both novel and familiar")
            if x.blank and (x.novel or x.familiar):
                self.issues.append(f"vial {i} ({x.chemical}) is a blank but also marked {x.role}")
        if fit(v, presented) >= 2:  # not a novelty day of this list, nor all familiar: say what it leaves out
            missing = [i for i, x in sorted(v.items()) if (x.novel or x.familiar) and i not in presented]
            if missing:
                self.notes.append(f"listed but not in this session: {', '.join(map(str, missing))}")
        if self.kind == "Familiar" and any(v[i].novel for i in presented if i in v):
            self.issues.append("the .mat says 'Familiar' but the list marks presented odors novel")
        fam, src = earlier_familiar(self.txt)
        if fam is not None:
            for i in sorted(presented):
                if i not in v or v[i].blank:
                    continue
                if v[i].familiar and i not in fam:
                    self.issues.append(f"vial {i} ({v[i].chemical}) is familiar in the list but in no earlier "
                                       f"'Familiar' session ({', '.join(src)})")
                if v[i].novel and i in fam:
                    self.issues.append(f"vial {i} ({v[i].chemical}) is novel in the list but was in an earlier "
                                       f"'Familiar' session")
            self.notes.append(f"history: {len(src)} earlier 'Familiar' session(s)")

    def role(self, i):
        return self.vials[i].role if self.vials and i in self.vials else "?"

    def name(self, i):
        return self.vials[i].chemical if self.vials and i in self.vials else ""

    def label(self, ids, chem=False):
        """'14 novel' (or '14 P.E.A novel' with chem); '' when no odor."""
        return ", ".join(f"{i}{' ' + self.name(i) if chem and self.name(i) else ''} {self.role(i)}" for i in ids)
