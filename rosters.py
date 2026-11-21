"""Read the rolling-stock duty diagrams (O.18 "dinasan") near the end of the GAPEKA PDF.

Each diagram row is one unit for one day: a locomotive (DINASAN LOKOMOTIF), a KRD set (DINASAN KRD) or a KRL
trainset (Commuter Line loops). Along the row the trains it works are drawn on a 24-hour axis; the train numbers in
left-to-right order are the unit's duty, including KA Dinas moves (R... empty trainset, L... light locomotive).

    python rosters.py GAPEKA-JAWA-2026.pdf gapeka.db     # prints a summary and a check against the timetable
"""
import re
import sqlite3
import sys
from collections import Counter, defaultdict

import pymupdf

TRAIN = re.compile(r"^D?([LR]?\d{1,4}F?(?:-\d)?)$")
KRL_TOKEN = re.compile(r"[A-Z][a-z]{1,4}|D?R?\d{3,5}F?(?:-\d)?")


def spans(page):
    out = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                if s["text"].strip():
                    x0, y0, x1, y1 = s["bbox"]
                    out.append((x0, y0, x1, y1, s["text"].strip(), round(s["size"], 2)))
    return out


def roster_pages(doc):
    """(page index, kind) for the duty diagrams"""
    out = []
    for i in range(doc.page_count):
        t = doc[i].get_text()
        u = t.upper()
        if "DINASAN LOKOMOTIF" in u:
            out.append((i, "loco"))
        elif "DINASAN (O.18) KRD" in u or "DINASAN KRD" in u:
            out.append((i, "krd"))
        elif ("O.18 COMMUTER LINE" in u or ("COMMUTER" in u and "LOOP" in u)) and re.search(r"\b\d{4}F?\b", t):
            out.append((i, "krl"))
    return out


def read_loco(page):
    """locomotive / KRD diagram: rows labelled by a number in the first column (larger font); train numbers above
    the bar ("57 (Sembrani)") and KA Dinas moves below it ("R57", "L57-2")"""
    S = spans(page)
    big = max((s[5] for s in S if s[0] < 60 and s[4].isdigit()), default=None)
    labels = sorted([s for s in S if s[0] < 60 and s[4].isdigit() and s[5] == big], key=lambda s: s[1])
    rows = []
    for k, lab in enumerate(labels):
        y = (lab[1] + lab[3]) / 2
        meta = " ".join(s[4] for s in sorted(S, key=lambda s: s[0]) if abs((s[1] + s[3]) / 2 - y) < 2 and s[0] < 200)
        band = [s for s in S if -11 < (s[1] + s[3]) / 2 - y < 11 and s[0] > 200 and s[5] < (big or 99)]
        seq = []
        for s in sorted(band, key=lambda s: s[0]):
            for part in re.split(r"\s+", s[4]):
                m = re.match(r"^(D?[LR]?\d{1,4}F?(?:-\d)?)(\(|$)", part)
                if m and not re.match(r"^\d{1,2}$", part) or re.match(r"^\d{1,4}F?$", part) and "(" in s[4]:
                    num = re.sub(r"^D(?=[LR])", "", m.group(1) if m else part)
                    seq.append((s[0], num))
        rows.append(dict(row=int(lab[4]), unit=meta, trains=[n for _, n in seq]))
    return rows


def read_krl(page):
    """KRL loop diagram: one row per trainset; the line above the bar alternates stations and train numbers
    ("Dp 1100 Boo 1145 Jakk ..."); the row label (loop number, series, depot) is on the line below it"""
    S = spans(page)
    lines = defaultdict(list)
    for s in S:
        lines[round((s[1] + s[3]) / 2)].append(s)
    rows = []
    for y in sorted(lines):
        L = sorted(lines[y], key=lambda s: s[0])
        text = " ".join(s[4] for s in L if s[0] > 175)
        toks = KRL_TOKEN.findall(text)
        nums = [t for t in toks if re.match(r"^D?R?\d{3,5}F?(?:-\d)?$", t)]
        sts = [t for t in toks if re.match(r"^[A-Z][a-z]{1,4}$", t)]
        if len(nums) >= 2 and len(sts) >= 2:
            lab = [s for y2 in range(y + 3, y + 10) for s in lines.get(y2, []) if s[0] < 175]
            meta = " ".join(s[4] for s in sorted(lab, key=lambda s: s[0]))
            rows.append(dict(row=len(rows) + 1, unit=meta, trains=[re.sub(r"^D(?=R)", "", t) for t in nums],
                             stations=sts))
    return rows


def read_rosters(doc):
    out = []
    for i, kind in roster_pages(doc):
        rows = read_loco(doc[i]) if kind == "loco" else read_krl(doc[i])     # KRD pages use the KRL layout
        for r in rows:
            r["trains"] = [t for t in r["trains"] if not re.fullmatch(r"0+", t)]      # "00000" placeholders
            if r["trains"]:
                out.append(dict(r, page=i + 1, kind=kind))
    return out


def check(rows, db):
    """consecutive trains of a unit should connect: the next train starts where the previous one ended"""
    first, last = {}, {}
    for no, st in db.execute("select train_no, station from stops order by train_no, seq"):
        first.setdefault(no, st)
        last[no] = st
    res = Counter()
    for r in rows:
        for a, b in zip(r["trains"], r["trains"][1:]):
            if a not in last or b not in first:
                res[(r["kind"], "train not in gapeka.db")] += 1
            elif last[a] == first[b]:
                res[(r["kind"], "connects")] += 1
            else:
                res[(r["kind"], "does not connect")] += 1
    return res



def read_consists(doc, trains):
    """Stamformasi tables (RANGKAIAN POKOK per depot, PDF ~1722-1730): per train number the cars of one trainset
    (SF = cars of all sets / RANGK = number of sets) and the locomotive class. {train_no: dict}"""
    out = {}
    pages = [i for i in range(doc.page_count) if "RANGKAIAN" in (t := doc[i].get_text()) and "RANGK" in t]
    for i in pages:
        W = doc[i].get_text("words")
        tables = []
        for h in (w for w in W if w[4] == "RANGK"):
            near = lambda txt: [w for w in W if w[4] == txt and abs(w[1] - h[1]) < 8]
            sf, nama, no = near("SF"), near("NAMA"), near("NO")
            if sf and nama and no:
                tables.append(dict(y=h[1], rangk=(h[0] + h[2]) / 2, sf=(sf[0][0] + sf[0][2]) / 2,
                                   no_x=min(no, key=lambda w: w[0])[2], nama_x=nama[0][0]))
        tables.sort(key=lambda t: t["y"])
        for k, tb in enumerate(tables):
            y1 = tables[k + 1]["y"] if k + 1 < len(tables) else 1e9
            rows = defaultdict(list)
            for w in W:
                if tb["y"] + 10 < w[1] < y1 - 2:
                    rows[round((w[1] + w[3]) / 2)].append(w)
            for ws in rows.values():
                ws.sort(key=lambda w: w[0])
                num = lambda x: next((w[4] for w in ws if abs((w[0] + w[2]) / 2 - x) < 6 and w[4].isdigit()), None)
                noka = " ".join(w[4] for w in ws if tb["no_x"] + 6 < w[0] and w[2] < tb["nama_x"] - 6)
                lok = " ".join(w[4] for w in ws if w[4] == "CC" or re.fullmatch(r"20\d", w[4]))
                r, sf = num(tb["rangk"]), num(tb["sf"])
                if r and sf and int(r) > 0:
                    for n in re.findall(r"\d{1,4}F?", noka):
                        if n in trains:
                            out.setdefault(n, dict(cars=round(int(sf) / int(r)), sets=int(r), total_cars=int(sf),
                                                   locomotive=lok or None, page=i + 1))
    return out

if __name__ == "__main__":
    d = pymupdf.open(sys.argv[1] if len(sys.argv) > 1 else "GAPEKA-JAWA-2026.pdf")
    db = sqlite3.connect(sys.argv[2] if len(sys.argv) > 2 else "gapeka.db")
    R = read_rosters(d)
    print(f"{len(R)} duty rows on {len({r['page'] for r in R})} pages:", Counter(r["kind"] for r in R))
    for k, v in sorted(check(R, db).items()):
        print("  ", k, v)
    for kind in ("loco", "krd", "krl"):
        for r in [r for r in R if r["kind"] == kind][:2]:
            print(kind, r["page"], r["unit"][:50], r["trains"][:14])
