"""Read the GAPEKA train-number index (Lampiran IV, "WAKTU PERJALANAN" tables near the end of the PDF).

Every train is listed with: number(s), REGULER / FAKULTATIF, train name, identity name, route (with via stations),
operator, distance, departure, arrival and running time, under a classification heading:
  KLASIFIKASI KERETA API  (KERETA API ANTAR KOTA / PERKOTAAN / DINAS)
  KELAS KERETA API        (ARGO, EKSEKUTIF, ..., COMMUTER LINE DAOP 1 JAK, DINAS RANGKAIAN DAOP 2 BD, DINAS LOKOMOTIF ...)
KA Dinas (empty trainset moves "R..." and locomotive moves "L...") appear only here and on the graph sheets.

    python train_index.py GAPEKA-JAWA-2026.pdf        # prints a summary
"""
import re
import sys
from collections import Counter

import pymupdf

JENIS = ("REGULER", "FAKULTATIF")
ROUTE = re.compile(r"^[A-Z]{1,5}(-[A-Z]{1,5})+$")
KM = re.compile(r"^\d{1,4},\d{3}$")
HHMM = re.compile(r"^\d{2}:\d{2}$")
DUR = re.compile(r"^\d{2}\.\d{2}$")


def cy(w):
    return (w[1] + w[3]) / 2


def index_pages(doc):
    return [i for i in range(doc.page_count)
            if "WAKTU PERJALANAN" in doc[i].get_text() and "KLASIFIKASI KERETA API" in doc[i].get_text()
            or ("WAKTU PERJALANAN" in doc[i].get_text() and "NAMA IDENTITAS KERETA API" in doc[i].get_text())]


def headings(page):
    """(y, kind, text) for 'KLASIFIKASI KERETA API : X' and 'KELAS KERETA API : Y' headings on a page"""
    out = []
    ws = page.get_text("words")
    rows = {}
    for w in ws:
        rows.setdefault(round(cy(w) / 2), []).append(w)
    lines = sorted((k * 2, " ".join(t[4] for t in sorted(v, key=lambda t: t[0]))) for k, v in rows.items())
    for y, t in lines:
        m = re.search(r"KLASIFIKASI KERETA API\s*:\s*(.+?)(?:\s+KELAS KERETA API|$)", t)
        if m:
            out.append((y, "klasifikasi", m.group(1).strip()))
        m = re.search(r"KELAS KERETA API\s*:\s*(.+?)(?:\s+REGULER\s*:|$)", t)
        if m:
            out.append((y, "kelas", m.group(1).strip()))
    return out


def read_index(doc):
    rows, klas, kelas = [], None, None
    for i in index_pages(doc):
        page = doc[i]
        ws = page.get_text("words")
        heads = headings(page)
        hdr = {w[4]: w[0] for w in ws if w[4] in ("IDENTITAS", "LINTAS")}
        anchors = sorted([w for w in ws if w[4] in JENIS], key=cy)
        for a in anchors:
            y = cy(a)
            for hy, kind, text in heads:                      # latest heading above this row
                if hy < y:
                    if kind == "klasifikasi":
                        klas = text
                    else:
                        kelas = text
            row = sorted([w for w in ws if abs(cy(w) - y) < 3.2], key=lambda w: w[0])
            left = [w for w in row if w[2] <= a[0]]
            right = [w for w in row if w[0] > a[2]]
            num = left[-1][4] if left else None
            urut = left[-2][4] if len(left) >= 2 else None
            ri = next((k for k, w in enumerate(right) if ROUTE.match(w[4])), None)
            if num is None or ri is None:
                continue
            names = right[:ri]
            idx = hdr.get("IDENTITAS")
            if idx is not None:
                name = " ".join(w[4] for w in names if w[0] < idx - 5)
                ident = " ".join(w[4] for w in names if w[0] >= idx - 5)
            else:
                name, ident = " ".join(w[4] for w in names), ""
            rest = right[ri + 1:]
            km = next((w[4] for w in rest if KM.match(w[4])), None)
            times = [w[4] for w in rest if HHMM.match(w[4])]
            dur = next((w[4] for w in rest if DUR.match(w[4])), None)
            op_ = " ".join(w[4] for w in rest if not (KM.match(w[4]) or HHMM.match(w[4]) or DUR.match(w[4])
                                                      or re.match(r"^\d+$", w[4])))
            rows.append(dict(page=i + 1, urut=urut, number=num, jenis=a[4], name=name.strip(" -"), identity=ident.strip(" -"),
                             route=right[ri][4], operator=op_, km=float(km.replace(",", ".")) if km else None,
                             dep=times[0] if times else None, arr=times[1] if len(times) > 1 else None,
                             duration=dur, klasifikasi=klas, kelas=kelas))
    return rows


def numbers(entry):
    """train numbers in an index entry: "841-842" -> 841, 842; "L2711-2-L2711" -> L2711-2, L2711;
    "L2609-2" -> L2609-2 (a locomotive move number with a part suffix)"""
    n = entry
    if re.match(r"^[LR]", n):
        parts = re.findall(r"[LR]\d+F?(?:-\d)?", n)
        return parts or [n]
    return [p for p in n.split("-") if p]


if __name__ == "__main__":
    d = pymupdf.open(sys.argv[1] if len(sys.argv) > 1 else "GAPEKA-JAWA-2026.pdf")
    R = read_index(d)
    print(f"{len(R)} index rows on {len({r['page'] for r in R})} pages")
    for k, v in Counter((r["klasifikasi"], r["kelas"]) for r in R).most_common():
        print(f"  {v:4d}  {k[0]} / {k[1]}")
    print(Counter(r["jenis"] for r in R))
    for r in R[:3] + [r for r in R if r["number"].startswith(("L", "R"))][:4]:
        print(r)
