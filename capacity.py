"""'PROGRAM KA DAN KAPASITAS LINTAS' (Lampiran IV, PDF pages 1866-1874): network diagrams per Daop. Station codes in
boxes, joined by track lines; for each section a text block of train counts per category 'KRL = 64/0' (regular /
facultative trains per day), 'P = 64/2' (program = scheduled trains), 'K = 192' (capacity, trains per day), linked to
its section by a dashed arrow.

Legend (page 1866): KOM = KA penumpang komersil, KRL = commuter line, BAND = KA bandara, EKO = KA penumpang ekonomi,
LOK EKO = KA lokal ekonomi, KRD = KA rel diesel patas, KRD EKO = KA rel diesel ekonomi, PARCEL = KA barang hantaran,
PTK = KA barang petikemas, BAR = batubara, BBM, SEMEN, PUPUK, TERNAK (ternak dan hasil bumi), D = KA dinas,
P = program, K = kapasitas, a/b = reguler / fakultatif. User: KOM / EKO = long-distance or local passenger trains other
than KRL; P = program (scheduled trains), K = capacity.

read(doc) -> blocks [dict(page, label, a, b, counts={category: (reg, fac)}, P=(reg, fac), K=int, sum_ok)]"""
import re

import pymupdf

LINE_RE = re.compile(r"^([A-Z][A-Z .]*?)\s*=\s*(\d+)(?:\s*/\s*(\d+))?$")


VAL_RE = re.compile(r"^(\d+)(?:/(\d+))?$")


def read_page(page, pno):
    """Every block carries its section as hidden white text ('KPB-AC', 'BKS-TB') just above its first line; the block
    is the column of 'LABEL = a/b' rows below it (same '=' column) down to the 'K = n' row. A few blocks print no
    category names (p1866 TPK-AC, AC-RJW): there the last row is K and the one above it P."""
    words = page.get_text("words")
    cy = lambda w: (w[1] + w[3]) / 2
    white = set()
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            for sp in l["spans"]:
                if sp["color"] == 16777215 and re.fullmatch(r"[A-Z]{1,5}-[A-Z]{1,5}", sp["text"].strip()):
                    white.add((sp["text"].strip(), round(sp["bbox"][0]), round(sp["bbox"][1])))
    labels = [w for w in words if re.fullmatch(r"[A-Z]{1,5}-[A-Z]{1,5}", w[4])
              and any(t == w[4] and abs(x - w[0]) < 2 and abs(y - w[1]) < 2 for t, x, y in white)]
    seen, blocks = set(), []
    for lab in labels:
        key = (lab[4], round(lab[0] / 4), round(lab[1] / 12))
        if key in seen:                                      # the label is sometimes printed twice
            continue
        seen.add(key)
        lx, y, rows = lab[0], cy(lab), []
        for _ in range(16):
            cand = [w for w in words if 2 < cy(w) - y < 15 and lx - 8 <= w[0] <= lx + 28 and w is not lab
                    and not re.fullmatch(r"[A-Z]{1,5}-[A-Z]{1,5}", w[4])]
            if not cand:
                break
            ny = cy(min(cand, key=lambda w: cy(w)))
            toks = [w[4] for w in sorted((w for w in words if abs(cy(w) - ny) < 1.6 and w[0] >= lx - 8), key=lambda w: w[0])]
            name, val, eq = [], None, False
            for t in toks:
                if t == "=" or t.endswith("=") or t.startswith("="):
                    head, _, tail = t.partition("=")
                    if head:
                        name.append(head)
                    eq = True
                    if tail and VAL_RE.match(tail):
                        val = tail
                        break
                    continue
                if VAL_RE.match(t) and (eq or name == ["K"]):
                    val = t
                    break
                if not eq and re.fullmatch(r"[A-Z]+", t):
                    name.append(t)
                elif not eq:
                    break
            if val is None:
                break
            m = VAL_RE.match(val)
            rows.append((" ".join(name), int(m.group(1)), int(m.group(2) or 0)))
            y = ny
            if rows[-1][0] == "K":
                break
        if rows and not any(r[0] in ("P", "K") for r in rows) and len(rows) >= 2:
            rows[-1] = ("K",) + rows[-1][1:]
            rows[-2] = ("P",) + rows[-2][1:]
        d = {r[0]: (r[1], r[2]) for r in rows}
        P, K = d.pop("P", None), d.pop("K", (None,))[0]
        blocks.append(dict(page=pno, label=lab[4], counts=d, P=P, K=K, x=round(lab[0]), y=round(lab[1])))
    parsed = {b["label"] for b in blocks if b["P"] is not None}
    blocks = [b for b in blocks if b["P"] is not None or b["label"] not in parsed]   # stray duplicate labels
    for b in blocks:
        b["a"], b["b"] = b["label"].split("-")
        reg = sum(v[0] for k, v in b["counts"].items() if k)
        b["sum_ok"] = b["P"] is not None and (not b["counts"] or "" in b["counts"] or reg == b["P"][0])
    return blocks


def read(doc, first=1866, last=1874):
    out = []
    for pno in range(first, last + 1):
        out += read_page(doc[pno - 1], pno)
    return out


if __name__ == "__main__":
    import sys
    doc = pymupdf.open("GAPEKA-JAWA-2026.pdf")
    pages = [int(sys.argv[1])] if len(sys.argv) > 1 else range(1866, 1875)
    for pno in pages:
        blocks = read_page(doc[pno - 1], pno)
        print(f"page {pno}: {len(blocks)} blocks, P = sum of regular counts in {sum(b['sum_ok'] for b in blocks)}")
        for b in blocks:
            print(f"   {b['label']:9s} P {b['P']} K {b['K']} {'' if b['sum_ok'] else 'SUM? '}{b['counts']}")
