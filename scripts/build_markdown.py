#!/usr/bin/env python3
"""Build per-person Markdown timetables from parse_roster.py output.

Usage: python3 build_markdown.py duties.json OUT_DIR
"""
import collections
import csv
import datetime as dt
import json
import os
import re
import sys

ROLE_ORDER = {
    "Chief Invigilator": 0,
    "Chief Invigilator (Understudy)": 1,
    "Invigilator": 2,
    "Invigilator (Understudy)": 3,
    "e-Exam Support": 4,
    "Standby e-Exam Support": 5,
    "Standby Chief Invigilator (POE)": 6,
    "Standby Chief Invigilator (Written)": 7,
}


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def parse_date(s):
    m = re.match(r"(\d{1,2})-([A-Za-z]{3})-(\d{2})", s)
    return dt.datetime.strptime(m.group(0), "%d-%b-%y").date()


def parse_time(s):
    a, b = [x.strip() for x in s.split("-")]
    return (dt.datetime.strptime(a, "%I:%M %p"), dt.datetime.strptime(b, "%I:%M %p"))


def fmt_t(t):
    return t.strftime("%-I:%M %p")


def short_venue(v):
    v = re.sub(r"^HQ, BLK C-SR\s*", "", v)
    v = re.sub(r"^HQ, BLK C-LAB\s*", "Lab ", v)
    return v or "—"


def clean_pe(pe):
    pe = re.sub(r"\*Report at\s*", "· reports at ", pe)
    pe = pe.replace("*CL", "(CL)")
    return pe.strip() or "—"


def pe_for(pe, time_label_raw):
    """Pick the right Presiding Examiner from team-split cells."""
    if "Team" not in pe:
        return clean_pe(pe)
    for m in re.finditer(r"(\d{1,2}:\d{2}\s*[AP]M\s*-\s*\d{1,2}:\d{2}\s*[AP]M)\s*Team\s*\d+\s*\(PE\):\s*"
                         r"(.*?)(?=\d{1,2}:\d{2}\s*[AP]M\s*-|$)", pe):
        if re.sub(r"\s+", "", m.group(1)) == re.sub(r"\s+", "", time_label_raw):
            return m.group(2).strip()
    return clean_pe(pe)


def md_escape(s):
    return s.replace("|", "\\|")


def enrich(recs):
    for r in recs:
        d = parse_date(r["date"])
        s, e = parse_time(r["time"])
        r["_d"], r["_s"], r["_e"] = d, s, e
        r["date_label"] = d.strftime("%a %-d %b %Y")
        r["time_label"] = f"{fmt_t(s)} – {fmt_t(e)}"
        r["report_label"] = fmt_t(s - dt.timedelta(hours=1))
        r["venue_short"] = short_venue(r["venue"])
    return recs


def dkey(r):
    return (r["role"], r["date"], r["time"], r["venue"])


def duty_line(r):
    where = f" · {r['venue_short']}" if r["venue"] else ""
    what = f" · {', '.join(c.split(' (')[0] for c in r['courses'])}" if r["courses"] else ""
    return f"{r['date_label']}, {r['time_label']} · {r['role']}{where}{what}"


def person_changes(old_rs, new_rs):
    """Duties removed from / added to one person between two roster versions."""
    o = {dkey(r): r for r in old_rs}
    n = {dkey(r): r for r in new_rs}
    order = lambda r: (r["_d"], r["_s"])
    removed = sorted((o[k] for k in o.keys() - n.keys()), key=order)
    added = sorted((n[k] for k in n.keys() - o.keys()), key=order)
    return removed, added


def main(src, out, source_label, prev=None, prev_label="previous roster"):
    recs = enrich(json.load(open(src)))
    old = enrich(json.load(open(prev))) if prev else []
    old_people = collections.defaultdict(list)
    for r in old:
        old_people[r["name"]].append(r)
    changes = {}

    # chief invigilator per venue-session, for invigilators' reference
    ci = collections.defaultdict(list)
    for r in recs:
        if r["role"] in ("Chief Invigilator", "Chief Invigilator (Understudy)"):
            label = r["name"] + (" (US)" if r["understudy"] else "")
            ci[(r["page"], r["date"], r["time"], r["venue"])].append(label)

    people = collections.defaultdict(list)
    for r in recs:
        people[r["name"]].append(r)

    os.makedirs(os.path.join(out, "timetables"), exist_ok=True)
    os.makedirs(os.path.join(out, "data"), exist_ok=True)

    index = []
    for name, rs in people.items():
        rs.sort(key=lambda r: (r["_d"], r["_s"], ROLE_ORDER.get(r["role"], 9)))
        main_duties = [r for r in rs if not r["role"].startswith("Standby")]
        standby = [r for r in rs if r["role"].startswith("Standby")]
        roles = collections.Counter(r["role"] for r in rs)
        index.append((name, len(main_duties), len(standby), roles, rs))

        L = [f"# {name} — Invigilation Timetable", "",
             f"SUSS July 2026 Semester Exams on Campus · Source: Invigilation Roster {source_label}", ""]
        if prev:
            removed, added = person_changes(old_people.get(name, []), rs)
            if removed or added:
                changes[name] = (removed, added)
                L += [f"> **Changed since {prev_label}:** "
                      + (f"{len(removed)} removed" if removed else "")
                      + (" · " if removed and added else "")
                      + (f"{len(added)} added" if added else "")
                      + " — see [Changes](#changes-since-" + slug(prev_label) + ") below.", ""]
        span = f"{rs[0]['date_label']} to {rs[-1]['date_label']}" if len(rs) > 1 else rs[0]["date_label"]
        L.append(f"**{len(main_duties)} {'duty' if len(main_duties) == 1 else 'duties'}**"
                 + (f" + **{len(standby)} standby**" if standby else "")
                 + f" · {span}")
        L.append("")
        L.append("Roles: " + ", ".join(f"{k} × {v}" for k, v in
                                        sorted(roles.items(), key=lambda kv: ROLE_ORDER.get(kv[0], 9))))
        L.append("")
        if main_duties:
            L += ["## Duties", "",
                  "| # | Date | Exam Time | Report By | Role | Room | Course / Paper | Chief Invigilator | Presiding Examiner |",
                  "|---|---|---|---|---|---|---|---|---|"]
            for i, r in enumerate(main_duties, 1):
                key = (r["page"], r["date"], r["time"], r["venue"])
                cis = ", ".join(ci.get(key, [])) or "—"
                if r["role"].startswith("Chief Invigilator"):
                    cis = "*(you)*" if not r["understudy"] else cis
                courses = "<br>".join(md_escape(c) for c in r["courses"]) or "—"
                L.append(f"| {i} | {r['date_label']} | {r['time_label']} | {r['report_label']} | {r['role']} | "
                         f"**{md_escape(r['venue_short'])}** | {courses} | {md_escape(cis)} | {md_escape(pe_for(r['pe'], r['time']))} |")
            L.append("")
        if standby:
            L += ["## Standby", "",
                  "| # | Date | Session | Report By | Role |",
                  "|---|---|---|---|---|"]
            for i, r in enumerate(standby, 1):
                L.append(f"| {i} | {r['date_label']} | {r['time_label']} | {r['report_label']} | {r['role']} |")
            L.append("")
        # back-to-back sessions: next session's reporting time falls before this one ends
        b2b = []
        for a, b in zip(main_duties, main_duties[1:]):
            if a["_d"] == b["_d"] and (b["_s"] - dt.timedelta(hours=1)) <= a["_e"]:
                b2b.append(f"{a['_d'].strftime('%a %-d %b')} ({fmt_t(a['_e'])} finish → {b['report_label']} report)")
        if b2b:
            L += ["> **Back-to-back:** " + "; ".join(b2b)
                  + ". The next session's reporting time falls before the previous one ends — "
                  "check the changeover with the EA Department.", ""]
        if name in changes:
            removed, added = changes[name]
            L += [f"## Changes since {prev_label}", ""]
            L += [f"- ~~{duty_line(r)}~~ — **removed**" for r in removed]
            L += [f"- {duty_line(r)} — **added**" for r in added]
            L.append("")
        L += ["## Reporting", "",
              "- Report **1 hour before** the exam starts at **Block C, Exam Room C.2.05 (Level 2)**.",
              "- Take the lift from **Lobby C3** (behind Starbucks); avoid the lift at Lobby C2 (restricted access).",
              "- Can't make a duty (main or standby)? Arrange a mutual swap or replacement and inform the EA Department "
              "(cc the person swapping).",
              "", "[← All invigilators](../README.md)", ""]
        with open(os.path.join(out, "timetables", slug(name) + ".md"), "w") as f:
            f.write("\n".join(L))

    # people who had duties in the previous roster but none now: keep a page so links don't break
    dropped = sorted((n for n in old_people if n not in people), key=str.lower)
    for name in dropped:
        rs = sorted(old_people[name], key=lambda r: (r["_d"], r["_s"]))
        changes[name] = (rs, [])
        L = [f"# {name} — Invigilation Timetable", "",
             f"SUSS July 2026 Semester Exams on Campus · Source: Invigilation Roster {source_label}", "",
             f"**No duties in roster {source_label.split(' ')[0]}.** All duties from roster {prev_label} were removed:", ""]
        L += [f"- ~~{duty_line(r)}~~" for r in rs]
        L += ["", "[← All invigilators](../README.md)", ""]
        with open(os.path.join(out, "timetables", slug(name) + ".md"), "w") as f:
            f.write("\n".join(L))

    if prev:
        C = [f"# Changes: {prev_label} → {source_label}", "",
             f"Duties that changed between roster {prev_label} and roster {source_label}. "
             "In the roster PDF, removed names are struck through with the replacement written beside them. "
             "Slots handed to **Temp Staff** show only as a removal.", "",
             f"- **{len(changes)} people affected** · "
             f"**{sum(len(v[0]) for v in changes.values())} duties removed** · "
             f"**{sum(len(v[1]) for v in changes.values())} duties added**", ""]
        if dropped:
            C += ["**No longer on the roster:** " + ", ".join(
                f"[{md_escape(n)}](timetables/{slug(n)}.md)" for n in dropped), ""]
        new_only = sorted((n for n in people if n not in old_people), key=str.lower)
        if new_only:
            C += ["**New to the roster:** " + ", ".join(
                f"[{md_escape(n)}](timetables/{slug(n)}.md)" for n in new_only), ""]
        C += ["## By person", ""]
        for name in sorted(changes, key=str.lower):
            removed, added = changes[name]
            C += [f"### [{md_escape(name)}](timetables/{slug(name)}.md)", ""]
            C += [f"- ~~{duty_line(r)}~~ — removed" for r in removed]
            C += [f"- {duty_line(r)} — **added**" for r in added]
            C.append("")
        with open(os.path.join(out, "CHANGES.md"), "w") as f:
            f.write("\n".join(C))

    index.sort(key=lambda x: x[0].lower())
    R = ["# SUSS July 2026 Exams — Invigilation Timetables", "",
         "Per-person timetables generated from **SUSS Jul 2026 Semester Exam on Campus Invigilation Roster "
         f"{source_label}**, covering exams from 10 to 20 Nov 2026 at SUSS HQ, Block C.", "",
         f"- **{len(index)} people**, **{sum(x[1] for x in index)} duties** and **{sum(x[2] for x in index)} standby slots**",
         "- Roles covered: Chief Invigilator (CI), CI understudy, Invigilator, e-Exam support, and named CI standbys. "
         "Presiding Examiners are not included.",
         "- Always check against the official roster — it may be revised again.", ""]
    if prev:
        R += [f"**What changed since roster {prev_label}:** see [CHANGES.md](CHANGES.md) — "
              f"{len(changes)} people affected.", ""]
    R += [
         "## Reporting", "",
         "All invigilation teams report **1 hour before** the exam starts at **Block C, Exam Room C.2.05 (Level 2)**. "
         "Take the lift from Lobby C3 (behind Starbucks). Contact the EA Department for swaps or replacements.", "",
         "## Legend", "",
         "PE – Presiding Examiner · CI – Chief Invigilator · I – Invigilator · CL – Course Leader · "
         "US – Understudy · POE – Online exam via Examena", "",
         "## Invigilators", "",
         "| Name | Duties | Standby | Main role | First – last date |",
         "|---|---|---|---|---|"]
    for name, nd, ns, roles, rs in index:
        main_role = max(roles.items(), key=lambda kv: (kv[1], -ROLE_ORDER.get(kv[0], 9)))[0]
        R.append(f"| [{md_escape(name)}](timetables/{slug(name)}.md) | {nd or '—'} | {ns or '—'} | {main_role} | "
                 f"{rs[0]['_d'].strftime('%-d %b')} – {rs[-1]['_d'].strftime('%-d %b')} |")
    R += ["", "## Data & scripts", "",
          "- `data/duties.csv` / `data/duties.json` — one row per person per duty (current roster)",
          "- `data/history/` — parsed duties from earlier roster versions, used for the change log",
          "- `scripts/parse_roster.py` — extracts duties from the roster PDF using its cell borders "
          "(so merged date/time/venue cells are matched correctly) and ignores struck-through names",
          "- `scripts/build_markdown.py` — builds these Markdown files and CHANGES.md", "",
          "Regenerate:", "", "```",
          "python3 scripts/parse_roster.py roster.pdf data/duties.json",
          "python3 scripts/build_markdown.py data/duties.json . --source \"v2 dated 23 Oct 2026\" \\",
          "    --prev data/history/duties_v1.json --prev-label \"v1 (16 Oct 2026)\"",
          "```", ""]
    with open(os.path.join(out, "README.md"), "w") as f:
        f.write("\n".join(R))

    fields = ["name", "role", "date_label", "time_label", "report_label", "venue_short", "courses", "pe", "page"]
    with open(os.path.join(out, "data", "duties.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Name", "Role", "Date", "Exam Time", "Report By", "Room", "Courses", "Presiding Examiner", "Roster Page"])
        for r in sorted(recs, key=lambda r: (r["name"].lower(), r["_d"], r["_s"])):
            w.writerow([r["name"], r["role"], r["date_label"], r["time_label"], r["report_label"],
                        r["venue_short"], "; ".join(r["courses"]), pe_for(r["pe"], r["time"]), r["page"]])
    print(len(index), "people")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("duties")
    ap.add_argument("out")
    ap.add_argument("--source", required=True, help='roster version label, e.g. "v2 dated 23 Oct 2026"')
    ap.add_argument("--prev", help="duties JSON from the previous roster version, for the change log")
    ap.add_argument("--prev-label", default="previous roster")
    a = ap.parse_args()
    main(a.duties, a.out, a.source, a.prev, a.prev_label)
