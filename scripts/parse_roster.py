#!/usr/bin/env python3
"""Parse the SUSS exam invigilation roster PDF into per-person duty records.

Uses the table's cell geometry (merged cells included) so that each
invigilator cell is matched to the date, time, venue, course(s) and PE
cells that span it vertically, rather than relying on text order.

Usage: python3 parse_roster.py ROSTER.pdf OUT.json
"""
import json
import re
import sys

import pdfplumber

HEADER_KEYS = {
    "DATE": "date",
    "TIME": "time",
    "SUBJECTS": "course",
    "STUD NOS. (by": "stud_course",
    "STUD NOS.": "stud",
    "NO. OF": "ninvig",
    "VENUE": "venue",
    "PRESIDING EXAMINER (PE)": "pe",
    "PRESIDING EXAMINER STANDBY": "pe_standby",
    "CHIEF INVIGILATOR (CI)": "ci",
    "CHIEF INVIGILATOR STANDBY": "ci_standby",
    "INVIGILATOR (I)": "inv",
    "INVIGILATOR STANDBY": "inv_standby",
}

TIME_RE = re.compile(r"\d{1,2}:\d{2}\s*[AP]M\s*-\s*\d{1,2}:\d{2}\s*[AP]M")
TEAM_RE = re.compile(
    r"(\d{1,2}:\d{2}\s*[AP]M\s*-\s*\d{1,2}:\d{2}\s*[AP]M)\s*Team\s*\d+\s*\((CI|I|PE)\):")


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def norm_time(s):
    s = clean(s)
    m = TIME_RE.search(s)
    return re.sub(r"\s*-\s*", " - ", m.group(0)) if m else s


def col_of(cell, cols):
    cx = (cell[0] + cell[2]) / 2
    for name, (x0, x1) in cols.items():
        if x0 - 0.5 <= cx <= x1 + 0.5:
            return name
    return None


def overlap(a, b):
    return max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def join_wraps(text):
    """Re-join names that wrapped onto a new line inside a cell."""
    return re.sub(r"\b(Binte|Bin|Mohamad|Mohamed)\s*\n\s*", r"\1 ", text)


def split_names(text, by_line=False):
    """Split a cell's text into (name, is_understudy) tuples.

    Invigilator cells are comma-separated and may wrap mid-name, so line
    breaks are treated as spaces unless by_line is set."""
    out = []
    text = join_wraps(text)
    if not by_line:
        text = text.replace("\n", " ")
    for part in re.split(r",|&|\n", text):
        p = clean(part)
        if not p or p.lower() == "temp staff":
            continue
        us = bool(re.search(r"\(US\)", p))
        p = clean(re.sub(r"\(US\)", "", p))
        if p:
            out.append((p, us))
    return out


def is_black(c):
    if c is None:
        return False
    if isinstance(c, (int, float)):
        return c == 0
    c = tuple(c)
    return len(c) in (1, 3) and all(v == 0 for v in c) or (len(c) == 4 and c[3] == 1 and not any(c[:3]))


def border_edges(page):
    """Only the black hairline rectangles are real cell borders; the coloured
    fills are drawn per spreadsheet row and would split merged cells."""
    h, v = [], []
    for r in page.rects:
        if not is_black(r.get("non_stroking_color")):
            continue
        w, ht = r["x1"] - r["x0"], r["bottom"] - r["top"]
        if ht <= 1.6 and w > 2:
            y = (r["top"] + r["bottom"]) / 2
            h.append({"x0": r["x0"], "x1": r["x1"], "top": y, "bottom": y,
                      "width": w, "height": 0, "orientation": "h", "object_type": "line"})
        elif w <= 1.6 and ht > 2:
            x = (r["x0"] + r["x1"]) / 2
            v.append({"x0": x, "x1": x, "top": r["top"], "bottom": r["bottom"],
                      "width": 0, "height": ht, "orientation": "v", "object_type": "line"})
    for l in page.lines:
        if l["x1"] - l["x0"] > l["bottom"] - l["top"]:
            h.append(l)
        else:
            v.append(l)
    return h, v


def page_cells(page):
    h, v = border_edges(page)
    tables = page.find_tables({
        "vertical_strategy": "explicit", "horizontal_strategy": "explicit",
        "explicit_vertical_lines": v, "explicit_horizontal_lines": h,
        "snap_tolerance": 2, "join_tolerance": 2, "intersection_tolerance": 2,
    })
    if not tables:
        return None, []
    t = max(tables, key=lambda t: len(t.cells))
    cells = []
    for c in t.cells:
        txt = page.crop(c).extract_text() or ""
        cells.append({"bbox": c, "text": txt})
    return t, cells


def parse(pdf_path):
    pdf = pdfplumber.open(pdf_path)
    records = []
    for pno, page in enumerate(pdf.pages, 1):
        t, cells = page_cells(page)
        if not cells:
            continue
        # header row: cells at the very top of the table
        top = min(c["bbox"][1] for c in cells)
        header = [c for c in cells if abs(c["bbox"][1] - top) < 1]
        cols = {}
        for c in header:
            h = clean(c["text"]).upper()
            key = None
            for k, v in HEADER_KEYS.items():
                if h.startswith(k.upper()):
                    key = v
                    break
            # "STUD NOS. (by Course)" vs "STUD NOS."
            if h.startswith("STUD NOS. (BY"):
                key = "stud_course"
            if key:
                cols[key] = (c["bbox"][0], c["bbox"][2])
        if "inv" not in cols:
            continue
        body = [c for c in cells if c["bbox"][1] >= top + 1]
        for c in body:
            c["col"] = col_of(c["bbox"], cols)

        def spanning(col, bb):
            cand = [c for c in body if c["col"] == col and overlap(c["bbox"], bb) > 0.5]
            return max(cand, key=lambda c: overlap(c["bbox"], bb)) if cand else None

        def all_spanning(col, bb):
            return [c for c in body if c["col"] == col and overlap(c["bbox"], bb) > 0.5]

        for c in body:
            if c["col"] not in ("ci", "inv", "ci_standby"):
                continue
            text = join_wraps(c["text"])
            if not clean(text):
                continue
            bb = c["bbox"]
            date_c = spanning("date", bb)
            time_c = spanning("time", bb)
            venue_c = spanning("venue", bb)
            vbb = venue_c["bbox"] if venue_c else bb
            pe_c = spanning("pe", vbb)
            courses = []
            for cc in sorted(all_spanning("course", vbb), key=lambda x: x["bbox"][1]):
                for line in re.findall(r"[A-Z]{2,4}\d{3}[A-Z]?\*?(?:\s*\([^)]*\))?",
                                       clean(cc["text"])):
                    courses.append(clean(line))
            stud_c = spanning("stud", vbb)
            ninv_c = spanning("ninvig", vbb)
            base = {
                "page": pno,
                "date": clean(date_c["text"]) if date_c else "",
                "time": norm_time(time_c["text"]) if time_c else "",
                "venue": clean(venue_c["text"]) if venue_c else "",
                "courses": list(dict.fromkeys(courses)),
                "students": clean(stud_c["text"]) if stud_c else "",
                "pe": clean(pe_c["text"]) if pe_c else "",
                "n_invig": clean(ninv_c["text"]) if ninv_c else "",
            }
            col = c["col"]

            # Team-split sessions (e.g. 4-hour IPM papers): explicit times per team
            if TEAM_RE.search(text):
                parts = TEAM_RE.split(text)
                # parts: [pre, time, role, names, time, role, names, ...]
                for i in range(1, len(parts), 3):
                    ttime, role, names = parts[i], parts[i + 1], parts[i + 2]
                    for name, us in split_names(names):
                        r = dict(base, time=norm_time(ttime),
                                 role="Chief Invigilator" if role == "CI" else "Invigilator",
                                 name=name, understudy=us)
                        records.append(r)
                continue

            if col == "ci_standby":
                # Named CI standbys appear as "POE / Standby 1: X" lists or
                # e-exam rows ("<name> Standby e-Exam Support: <name>").
                mode = None
                lines = [clean(l) for l in text.split("\n") if clean(l)]
                pre_support = True
                for ln in lines:
                    if ln in ("POE", "Written"):
                        mode = ln
                        continue
                    m = re.match(r"Standby \d+:\s*(.+)", ln)
                    if m:
                        records.append(dict(base, venue="", courses=[], students="", pe="",
                                            role=f"Standby Chief Invigilator ({mode or 'All'})",
                                            name=clean(m.group(1)), understudy=False))
                        continue
                    m = re.match(r"Standby e-Exam Support:\s*(.*)", ln)
                    if m:
                        pre_support = False
                        if m.group(1):
                            records.append(dict(base, role="Standby e-Exam Support",
                                                name=clean(m.group(1)), understudy=False))
                        continue
                    if not pre_support:
                        records.append(dict(base, role="Standby e-Exam Support",
                                            name=ln, understudy=False))
                    else:
                        # name above "Standby e-Exam Support" in an e-exam row:
                        # second Main e-Exam Support person (wrapped from CI cell)
                        records.append(dict(base, role="e-Exam Support",
                                            name=ln, understudy=False))
                continue

            if col == "ci":
                lines = [clean(l) for l in text.split("\n") if clean(l)]
                support = False
                for ln in lines:
                    m = re.match(r"Main e-Exam Support:\s*(.*)", ln)
                    if m:
                        support = True
                        if m.group(1):
                            records.append(dict(base, role="e-Exam Support",
                                                name=clean(m.group(1)), understudy=False))
                        continue
                    for name, us in split_names(ln, by_line=True):
                        records.append(dict(base,
                                            role="e-Exam Support" if support else
                                            ("Chief Invigilator (Understudy)" if us else "Chief Invigilator"),
                                            name=name, understudy=us))
                continue

            if col == "inv":
                for name, us in split_names(text):
                    records.append(dict(base, role="Invigilator (Understudy)" if us else "Invigilator",
                                        name=name, understudy=us))
    return records


if __name__ == "__main__":
    recs = parse(sys.argv[1])
    with open(sys.argv[2], "w") as f:
        json.dump(recs, f, indent=1)
    print(len(recs), "records")
