"""Extract KAI Commuter published timetables (station x train grids, either orientation) to rows:
   (train_label, order, station_code, sub, value)   value = 'HH:MM' | 'Ls' | 'Lanjut'
"""
import re
import sys
import json
import pymupdf

TIME = re.compile(r"^\d{2}[.:]\d{2}$")
CODE = re.compile(r"^\(([A-Z]{1,5})\)$")
TRAIN = re.compile(r"^\d{3}(-\d{3})?$")


def cx(w):
    return (w[0] + w[2]) / 2


def cy(w):
    return (w[1] + w[3]) / 2


def page_rows(page):
    ws = page.get_text("words")
    codes = [w for w in ws if CODE.match(w[4])]
    trains = [w for w in ws if TRAIN.match(w[4])]
    subs = [w for w in ws if w[4] in ("Datang", "Berangkat")]
    vals = [w for w in ws if TIME.match(w[4]) or w[4] in ("Ls", "Lanjut")]
    out = []
    for v in vals:
        x, y = cx(v), cy(v)
        # vertical layout: code on the same row, left of the value
        left = [c for c in codes if abs(cy(c) - y) < 4 and cx(c) < x]
        above = [c for c in codes if cy(c) < y and abs(cx(c) - x) < 14]
        sub = ""
        if left and not above:
            code = max(left, key=cx)
            tr = [t for t in trains if cy(t) < y and abs(cx(t) - x) < 20]
            if not tr:
                continue
            t = max(tr, key=cy)
            order = y
        else:
            sb_near = [w for w in subs if cy(w) < y and abs(cx(w) - x) < 14]
            if sb_near and (not above or max(map(cy, sb_near)) > max(map(cy, above))):
                above = []
            if not above:
                # SBI Datang/Berangkat sub-columns: the code is centred over both
                sb = [s for s in subs if cy(s) < y and abs(cx(s) - x) < 14]
                if not sb:
                    continue
                s = max(sb, key=cy)
                sub = s[4]
                above = [c for c in codes if cy(c) < cy(s) and abs(cx(c) - cx(s)) < 40]
                if not above:
                    continue
            code = max(above, key=cy)
            tr = [t for t in trains if abs(cy(t) - y) < 6 and cx(t) < x]
            if not tr:
                continue
            t = min(tr, key=lambda t: (abs(cy(t) - y), -cx(t)))
            order = x
        out.append((t[4], round(order, 1), CODE.match(code[4]).group(1), sub, v[4].replace(":", ".")))
    return out


if __name__ == "__main__":
    rows = []
    for f in sys.argv[1:]:
        d = pymupdf.open(f)
        for p in d:
            for r in page_rows(p):
                rows.append((f.split("/")[-1][:8], p.number) + r)
    json.dump(rows, sys.stdout)
