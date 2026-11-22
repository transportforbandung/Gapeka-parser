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


# ------------------------------------------------------------------ rangkaian pokok (Buku Dukungan Sarana, Lampiran III)
POKOK_TOKEN = re.compile(r"[RL]\d+F?-\d(?=$|[-/\s])|[RL]?\d+F?")


def read_pokok(doc, first=1674, last=1824):
    """'RANGKAIAN POKOK' tables of KRL / KRD sets (user: the trainset duties, KRL and KRD in their own sections):
    one row per set with the chain of trains it works in the day ('1125-1126-1187-...', KA Dinas 'R1505-2' included)
    and SF = cars per set. Rows may wrap over several lines above / below the row line (Basoetta).
    Returns [dict(kind, page, row, trains, sf)]."""
    out = []
    for pno in range(first, last + 1):
        page = doc[pno - 1]
        text = " ".join(page.get_text().split())
        m = re.search(r"RANGKAIAN POKOK (KRL|KRD|KA LOKAL)", text)
        if not m:
            continue
        kind = {"KRL": "krl", "KRD": "krd", "KA LOKAL": "krl"}[m.group(1)]
        words = page.get_text("words")
        sf = [w for w in words if w[4] == "SF"]
        nama = [w for w in words if w[4] == "NAMA"]
        if not sf or not nama:
            continue
        sf_x = (sf[0][0] + sf[0][2]) / 2
        head_y = max(sf[0][3], nama[0][3])
        no_w = min((w for w in words if w[4] == "NO" and w[1] < head_y + 2), key=lambda w: w[0])
        loop_w = [w for w in words if w[4] == "LOOP" and w[1] < head_y + 2]
        skip_x = [no_w[0]] + [w[0] for w in loop_w]
        x_chain0, x_chain1 = no_w[2] + 4, nama[0][0] - 3
        # rows: a number in the NO column (header 'NO') with an SF figure on the same line
        anchors = []
        for w in words:
            if w[1] > head_y and abs(w[0] - no_w[0]) < 10 and re.fullmatch(r"\d+", w[4]):
                cy = (w[1] + w[3]) / 2
                s = [v for v in words if abs((v[0] + v[2]) / 2 - sf_x) < 12 and re.fullmatch(r"\d+", v[4])
                     and abs((v[1] + v[3]) / 2 - cy) < 4]
                if s:
                    anchors.append((cy, int(w[4]), int(s[0][4])))
        if not anchors:
            continue
        toks = defaultdict(list)
        for w in words:
            cy = (w[1] + w[3]) / 2
            if w[1] <= head_y or not (x_chain0 < w[0] < x_chain1) or not re.search(r"\d", w[4]):
                continue
            a = min(anchors, key=lambda r: abs(r[0] - cy))
            if abs(a[0] - cy) > 14:
                continue
            if re.fullmatch(r"\d+", w[4]) and any(abs(w[0] - x) < 10 for x in skip_x):
                continue                                         # the NO / LOOP numbers of the row
            toks[a].append((round(cy), w[0], w[4]))
        for a in anchors:
            parts = [t[2] for t in sorted(toks[a])]
            chain = "".join(p if p.startswith(("-", "/")) or (i and parts[i - 1].endswith(("-", "/"))) else
                            (" " + p if i else p) for i, p in enumerate(parts))
            trains = POKOK_TOKEN.findall(chain)
            # drop the loop number if it slipped in first (it sits just left of the chain on some pages)
            out.append(dict(kind=kind, page=pno, row=a[1], trains=trains, sf=a[2]))
    # the KRL sets are listed twice (by depot, pages 1787-1790, and by line, 1791-1795): keep each chain once
    seen, uniq = set(), []
    for r in out:
        k = tuple(r["trains"])
        if k and k not in seen:
            seen.add(k)
            uniq.append(r)
    return uniq


def read_pokok_freight(doc, first=1674, last=1824):
    """'RANGKAIAN POKOK KA BARANG' tables (Buku Dukungan Sarana, pages 1758-1766): per freight set its trains
    ('2519 / 2520', '2610-2611-2614 / 2613-2612-2609', groups joined by '=' or '//'), the locomotive class (JENIS LOK,
    'CC 206'), SF = wagons per set, the wagon type (column of the daily wagon need) and the number of sets (Jumlah
    RANG). A page can hold several tables (one per depot). One NO row can hold several sets, each with its own SF on
    its own line (p1765 row 2); an SF cell can also span two train lines (p1760 row 2): every SF figure is one set,
    train lines go to the nearest SF line, locomotive cells may be merged over rows (taken from the row above).
    Returns [dict(kind='freight', page, row, trains, sf, locomotive, wagon, sets)]."""
    out = []
    for pno in range(first, last + 1):
        page = doc[pno - 1]
        if "RANGKAIAN POKOK KA BARANG" not in " ".join(page.get_text().split()):
            continue
        words = page.get_text("words")
        ends = sorted(w[1] for w in words if w[4] == "Stamformasi")
        full_rules = []
        for dr in page.get_drawings():
            for it in dr["items"]:
                r = it[1] if it[0] == "re" else (pymupdf.Rect(it[1], it[2]) if it[0] == "l" else None)
                if r is not None and abs(r.y1 - r.y0) < 1.5 and r.x0 < 40 and r.x1 > page.rect.width * 0.9:
                    full_rules.append((r.y0 + r.y1) / 2)
        for nomor in sorted((w for w in words if w[4] == "NOMOR"), key=lambda w: w[1]):
            y0 = nomor[1] - 30
            y1 = min([e for e in ends if e > nomor[1]] or [page.rect.height])
            tw = [w for w in words if y0 < w[1] < y1]
            def head(*names):
                c = [w for w in tw if w[4] in names and w[1] < nomor[1] + 12]
                return min(c, key=lambda w: w[1]) if c else None
            no_w, sf_w, nama_w, jenis_w = head("NO", "NO."), head("SF"), head("NAMA"), head("JENIS")
            rang_w, dinas_w, km_w = head("RANG"), head("DINAS"), head("KM")
            if not (no_w and sf_w and nama_w and jenis_w):
                continue
            hl = [w for w in tw if w[1] < sf_w[3] + 12 and w[0] > sf_w[0] and w[4] in ("T", "PULP", "AFTUR")]
            head_y = max([w[3] for w in hl] + [sf_w[3]])
            sf_x = (sf_w[0] + sf_w[2]) / 2
            labels = []
            hw = sorted((w for w in tw if sf_w[1] - 4 < w[1] < head_y and w[0] > sf_w[2]), key=lambda w: (round(w[1]), w[0]))
            for i, w in enumerate(hw):
                if w[4] in ("GD", "GK", "GB", "GT") and i + 1 < len(hw) and re.match(r"[\d/]+$", hw[i + 1][4]):
                    labels.append(((w[0] + hw[i + 1][2]) / 2, f"{w[4]} {hw[i + 1][4]}T"))
            cyw = lambda w: (w[1] + w[3]) / 2
            body = [w for w in tw if w[1] > head_y]
            num = lambda w: re.fullmatch(r"\d+", w[4])
            rows = sorted((cyw(w), int(w[4])) for w in body if abs(w[0] - no_w[0]) < 10 and num(w))
            sfs = sorted((cyw(w), int(w[4])) for w in body if abs((w[0] + w[2]) / 2 - sf_x) < 10 and num(w))
            if not sfs:
                continue
            x_chain0, x_chain1 = no_w[2] + 3, nama_w[0] - 8
            x_loco1 = (km_w[0] if km_w else sf_w[0]) - 4
            # row borders: full-width rules (partial rules only split a row's sub-lines)
            rules = sorted({round(y, 1) for y in full_rules if y0 < y < y1})
            cell = lambda cy: sum(1 for y in rules if y < cy)
            chains, locos = defaultdict(list), []
            for w in body:
                cy = cyw(w)
                if x_chain0 < w[0] < x_chain1 and not (abs(w[0] - no_w[0]) < 10 and num(w)):
                    cand = [s for s in sfs if cell(s[0]) == cell(cy)] if rules else sfs
                    if not cand:
                        continue
                    a = min(cand, key=lambda s: abs(s[0] - cy))
                    if abs(a[0] - cy) <= 14:
                        chains[a].append((round(cy), w[0], w[4]))
                elif jenis_w[0] - 6 < w[0] < x_loco1:
                    locos.append((round(cy), w[0], w[4]))
            loco_lines = defaultdict(list)
            for cy, x, t in sorted(locos):
                loco_lines[cy].append(t)
            prev_loco = None
            for a in sfs:
                parts = [t[2] for t in sorted(chains[a])]
                trains = POKOK_TOKEN.findall(" ".join(parts).replace("/", " ").replace("=", " "))
                near = [(abs(cy - a[0]), " ".join(ts)) for cy, ts in loco_lines.items() if abs(cy - a[0]) <= 8]
                loco = min(near)[1] if near else prev_loco
                prev_loco = loco
                if not trains:
                    continue                                     # spare sets (WESEL STEL ...)
                row = min(rows, key=lambda r: abs(r[0] - a[0]) if r[0] <= a[0] + 6 else 1e9)[1] if rows else None
                same = [w for w in body if abs(cyw(w) - a[0]) < 4]
                rang = [int(w[4]) for w in same if rang_w and abs((w[0] + w[2]) / 2 - (rang_w[0] + rang_w[2]) / 2) < 10
                        and num(w)]
                wag = None
                for w in sorted(same):
                    if labels and num(w) and sf_w[2] + 4 < w[0] < (dinas_w[0] - 4 if dinas_w else 9e9):
                        wag = min(labels, key=lambda l: abs(l[0] - (w[0] + w[2]) / 2))[1]
                        break
                out.append(dict(kind="freight", page=pno, row=row, trains=trains, sf=a[1], sets=rang[0] if rang else None,
                                locomotive=loco, wagon=wag))
    return out
