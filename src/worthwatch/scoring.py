"""Score the tool against your own labels.

Your labels are a small JSON file, labels.json in the config folder: every video of a few
digests, with the verdict the tool gave and the one you give it, KEEP or SKIP. With it,
`worthwatch score` says how often a YES or an OPPORTUNITY is one you would keep (precision) and
how many of your keeps the tool found (recall), and `worthwatch score --rules` re-scores the
stored write-ups with today's rules, so a change to your rules.toml is measured before you
trust it.

    worthwatch score build DIGEST-2026-01-05.md DIGEST-2026-01-06.md
        writes labels.json with every video of those digests and "your_verdict": "?"
    worthwatch score keep <video id> [...]     marks those KEEP (the rest become SKIP)
    worthwatch score                            the tool's recorded verdicts against yours
    worthwatch score --rules                    today's rules over the stored write-ups

The digest reader is here too: the digest is the only place every video of a day appears with
the verdict a reader actually saw, so it is what the labels are built from. core.write_digest()
is the writer and rows_in_digest() is the reader, and the two have to agree about the shapes
below. The title is NOT a key: a channel can retitle a video between its listing and its
write-up, so a row is matched by its video id.
"""
import json
import re
from pathlib import Path

POSITIVES = ("YES", "OPPORTUNITY")
UNLABELLED = "?"

RUN_RE = re.compile(r"^## Run at ", re.M)
DAY_TABLE_RE = re.compile(r"<!-- TABLE-START -->.*?<!-- TABLE-END -->", re.DOTALL)
# The verdicts the day table can print for a video nobody read. They are not verdicts about
# the video, and a row carrying one is not a judgment, so the scorer must not read it as one.
NOT_A_VERDICT = ("NOT READ", "NOT WRITTEN", "WAITING")
VERDICT_CELL_RE = re.compile(r"^(YES|OPPORTUNITY|MAYBE|NO)\b(?:\s*\(the listing said "
                             r"(YES|OPPORTUNITY|MAYBE|NO)\))?\s*$")
LINK_RE = re.compile(r"^\[(.*)\]\((https?://[^)]+)\)$")
NO_ENTRY_RE = re.compile(r"^- \[(?P<title>.*)\]\((?P<url>https?://[^)]+)\) "
                         r"\((?P<channel>.*?)\) - (?P<reason>.*)$")
# A read-then-dropped NO says "(read, then dropped)"; an older digest said what the listing
# said instead, so the tail is read in both shapes; the group is None for the newer one.
LISTING_TAIL_RE = re.compile(r"\s*\((?:read; the listing said (YES|OPPORTUNITY|MAYBE|NO)"
                             r"|read, then dropped)\)\s*$")
SUMMARY_HEAD_RE = re.compile(r"^#### \[(YES|OPPORTUNITY|MAYBE|NO)\] (?P<rest>.*)$")
# The tail write_digest() appends to a summary heading: (channel, backend, 12.3s[ [chunked]]).
SUMMARY_TAIL_RE = re.compile(r"\s*\([^()]*,\s*[^,()]*,\s*[\d.]+s(?:\s*\[chunked\])?\)\s*$")
SECTION6_RE = re.compile(r"^##\s*6\b")
SECTION_RE = re.compile(r"^##\s")
VIDEO_ID_RE = re.compile(r"[?&]v=([\w-]{6,})")


def unescape_cell(text):
    """md_cell() escapes a pipe so it cannot open a column. Undo that, and only that."""
    return text.replace("\\|", "|")


def split_cells(line):
    """A markdown row's cells. Splits on unescaped pipes only, so a title carrying "\\|"
    stays one cell. Written out rather than str.split("|") because that is the bug this
    reader exists to avoid having."""
    cells, current, i = [], [], 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line) and line[i + 1] == "|":
            current.append("\\|")
            i += 2
            continue
        if c == "|":
            cells.append("".join(current))
            current = []
        else:
            current.append(c)
        i += 1
    cells.append("".join(current))
    return [c.strip() for c in cells]


def video_id(url):
    m = VIDEO_ID_RE.search(url or "")
    return m.group(1) if m else None


def applies_state(summary_text):
    """What section 6 of one summary amounts to: "nothing", "items", or "unverified" when
    the guard could not find a section 6 to check at all."""
    if "SECTION 6 NOT VERIFIED" in summary_text:
        return "unverified"
    lines = summary_text.splitlines()
    start = next((i for i, l in enumerate(lines) if SECTION6_RE.match(l.strip())), None)
    if start is None:
        return "unverified"
    end = next((i for i in range(start + 1, len(lines)) if SECTION_RE.match(lines[i].strip())),
               len(lines))
    body = [l.strip() for l in lines[start + 1:end]]
    # The guard's own banners start with ">"; they are not what the section says.
    body = [l for l in body if l and not l.startswith(">")]
    if not body:
        return "nothing"
    if all(re.fullmatch(r"(?:most applicable:\s*)?(?:nothing|none|n/?a|not applicable)[\s.:,;!-]*",
                        l, re.I) for l in body):
        return "nothing"
    return "items"


def summaries_in(block):
    """{title: (verdict, section-6 state)} for one run block of a digest."""
    found = {}
    lines = block.splitlines()
    heads = [(i, m) for i, l in enumerate(lines) for m in [SUMMARY_HEAD_RE.match(l)] if m]
    for n, (i, m) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        title = SUMMARY_TAIL_RE.sub("", m.group("rest")).strip()
        found[title] = (m.group(1), applies_state("\n".join(lines[i:end])))
    return found


def rows_in_digest(path):
    """Every video in one digest, in file order. Each row is a dict:
    date, verdict, listing_verdict, channel, title, url, id, reason, fetched,
    applies_digest, confidence."""
    path = Path(path)
    date = re.search(r"DIGEST-(\d{4}-\d{2}-\d{2})", path.name).group(1)
    text = path.read_text(encoding="utf-8")
    rows = []
    # Two shapes of file, and both read: a day with ONE table, regenerated above the run
    # sections between TABLE-START and TABLE-END, and an older shape where each run appended its
    # own table inside its "## Run at" block. They never overlap.
    starts = [m.start() for m in RUN_RE.finditer(text)]
    blocks = []
    day = DAY_TABLE_RE.search(text)
    if day:
        blocks.append(day.group(0))
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(text)
        blocks.append(text[start:end])
    for block in blocks:
        summaries = summaries_in(block)
        for line in block.splitlines():
            line = line.rstrip()
            if line.startswith("|"):
                cells = split_cells(line)
                if len(cells) < 6:
                    continue
                # Two table shapes: Verdict | Channel | Title | Reason, and an older
                # Verdict | Confidence | Channel | Title | Reason. The cell count decides.
                if len(cells) >= 7:
                    _, verdict_cell, confidence, channel, title_cell, reason = cells[:6]
                else:
                    _, verdict_cell, channel, title_cell, reason = cells[:5]
                    confidence = None
                vm = VERDICT_CELL_RE.match(verdict_cell)
                lm = LINK_RE.match(title_cell)
                if not vm and lm and verdict_cell.startswith(NOT_A_VERDICT):
                    # A row for a video that was never read, or read and never written. It
                    # is scored as what it is: not a positive, and not a MAYBE somebody
                    # arrived at either.
                    state = next(s for s in NOT_A_VERDICT if verdict_cell.startswith(s))
                    rows.append({
                        "date": date, "verdict": state, "listing_verdict": None,
                        "channel": unescape_cell(channel), "title": unescape_cell(lm.group(1)),
                        "url": lm.group(2), "id": video_id(lm.group(2)),
                        "reason": unescape_cell(reason), "fetched": state == "NOT WRITTEN",
                        "applies_digest": None, "confidence": None})
                    continue
                if not vm or not lm:
                    continue          # the header row and the |---| separator
                title = unescape_cell(lm.group(1))
                got = summaries.get(title)
                rows.append({
                    "date": date, "verdict": vm.group(1), "listing_verdict": vm.group(2),
                    "channel": unescape_cell(channel), "title": title, "url": lm.group(2),
                    "id": video_id(lm.group(2)), "reason": unescape_cell(reason),
                    "fetched": True, "applies_digest": got[1] if got else None,
                    "confidence": None if confidence in (None, "", "unknown")
                                  else int(confidence) if confidence.isdigit() else confidence,
                })
                continue
            m = NO_ENTRY_RE.match(line)
            if m:
                reason = m.group("reason")
                tail = LISTING_TAIL_RE.search(reason)
                rows.append({
                    "date": date, "verdict": "NO",
                    "listing_verdict": tail.group(1) if tail else None,
                    "channel": m.group("channel"), "title": m.group("title"),
                    "url": m.group("url"), "id": video_id(m.group("url")),
                    "reason": LISTING_TAIL_RE.sub("", reason),
                    "fetched": bool(tail), "applies_digest": None, "confidence": None,
                })
    return rows


def rows_in_digests(paths):
    rows = []
    for p in paths:
        rows.extend(rows_in_digest(p))
    return rows


# --- the labels file ------------------------------------------------------------------------
def history_by_id(history_path):
    """{video id: the first history.jsonl row that carries a write-up}."""
    rows = {}
    path = Path(history_path)
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        vid = video_id(entry.get("url"))
        if vid and entry.get("summary") and vid not in rows:
            rows[vid] = entry
    return rows


def build_labels(digest_paths, history_path, keep=()):
    """The labels file's contents for these digests: every video, the verdict the tool gave,
    what its section 6 came to (joined from history.jsonl by video id), and your verdict:
    KEEP for the ids in `keep`, "?" for the rest until you mark them."""
    rows = rows_in_digests(digest_paths)
    hist = history_by_id(history_path)
    keep = set(keep)
    out = []
    for r in rows:
        entry = hist.get(r["id"])
        summary = entry.get("summary") if entry else None
        out.append({
            "id": r["id"], "date": r["date"], "channel": r["channel"], "title": r["title"],
            "url": r["url"], "tool_verdict": r["verdict"], "listing_verdict": r["listing_verdict"],
            "tool_reason": r["reason"],
            "applies": applies_state(summary) if summary else None,
            "has_summary": summary is not None,
            "your_verdict": "KEEP" if r["id"] in keep else UNLABELLED,
        })
    return {"source": [Path(p).name for p in digest_paths], "videos": out}


def load_labels(path):
    """{id: label row}. FileNotFoundError when there is no labels file yet."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {v["id"]: v for v in data["videos"]}


def mark_keeps(path, ids):
    """Mark these ids KEEP and every other unlabelled video SKIP; returns how many changed."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    ids, changed = set(ids), 0
    for v in data["videos"]:
        want = "KEEP" if v["id"] in ids else (v["your_verdict"] if v["your_verdict"] != UNLABELLED
                                             else "SKIP")
        if v["your_verdict"] != want:
            v["your_verdict"] = want
            changed += 1
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return changed


# --- the score -----------------------------------------------------------------------------
def rate(hit, total):
    return None if not total else hit / total


def pct(value):
    return "n/a" if value is None else f"{value * 100:.1f}%"


def score(golden, scored, opportunity_never_keep=False):
    """Every number, as data, so the caller decides how to print it.

    A positive is a YES or an OPPORTUNITY. Precision is how many positives you would keep;
    recall is how many of your keeps the tool found. Both are reported for the two verdicts
    together and for each on its own, because they fail differently. A labelled video the
    scored file never mentions is MISSING, not a miss, and a video still marked "?" is not
    counted either way."""
    def yours(vid):
        row = golden[vid]
        if opportunity_never_keep and row["tool_verdict"] == "OPPORTUNITY":
            return "SKIP"
        return row["your_verdict"]

    golden = {v: g for v, g in golden.items() if g.get("your_verdict") != UNLABELLED}
    keeps = [v for v in golden if yours(v) == "KEEP"]
    judged = [v for v in golden if v in scored]
    missing = [v for v in golden if v not in scored]
    extra = [v for v in scored if v not in golden]

    result = {"golden": len(golden), "judged": len(judged), "missing": missing,
              "extra": extra, "keeps": len(keeps), "by_verdict": {}, "false_positives": [],
              "false_negatives": [], "upgraded_empty": {"KEEP": [], "SKIP": []}}
    for label, wanted in (("YES+OPPORTUNITY", POSITIVES), ("YES", ("YES",)),
                          ("OPPORTUNITY", ("OPPORTUNITY",))):
        called = [v for v in judged if scored[v]["verdict"] in wanted]
        tp = [v for v in called if yours(v) == "KEEP"]
        fp = [v for v in called if yours(v) != "KEEP"]
        fn = [v for v in keeps if v in scored and scored[v]["verdict"] not in wanted]
        result["by_verdict"][label] = {
            "called": len(called), "tp": len(tp), "fp": len(fp), "fn": len(fn),
            "precision": rate(len(tp), len(called)), "recall": rate(len(tp), len(keeps)),
        }
        if label == "YES+OPPORTUNITY":
            result["false_positives"] = fp
            result["false_negatives"] = fn
    # Upgraded from MAYBE with section 6 saying nothing: promoted on the strength of a
    # transcript read, and then nothing in it applied to anything.
    for vid in judged:
        s = scored[vid]
        if s["verdict"] in POSITIVES and s["listing_verdict"] == "MAYBE" \
                and s["applies"] == "nothing":
            result["upgraded_empty"][yours(vid)].append(vid)
    return result


def report(golden, result, title):
    lines = [f"=== {title} ===",
             f"{result['judged']} of {result['golden']} labelled video(s) judged; "
             f"{result['keeps']} are KEEP"]
    if result["missing"]:
        lines.append(f"  MISSING from what was scored ({len(result['missing'])}): "
                     + ", ".join(result["missing"]))
    if result["extra"]:
        lines.append(f"  not in your labels ({len(result['extra'])}): "
                     + ", ".join(result["extra"]))
    lines += ["", f"{'verdict':<16} {'called':>6} {'kept':>5} {'false':>6} {'missed':>7} "
                  f"{'precision':>10} {'recall':>8}"]
    for label, m in result["by_verdict"].items():
        lines.append(f"{label:<16} {m['called']:>6} {m['tp']:>5} {m['fp']:>6} {m['fn']:>7} "
                     f"{pct(m['precision']):>10} {pct(m['recall']):>8}")
    up = result["upgraded_empty"]
    lines += ["", f"upgraded from MAYBE with section 6 saying nothing: "
                  f"{len(up['KEEP']) + len(up['SKIP'])}  ({len(up['SKIP'])} you would skip, "
                  f"{len(up['KEEP'])} you would keep)"]
    for vid in up["KEEP"]:
        lines.append(f"    KEEP, so demoting it costs a true positive: "
                     f"{golden[vid]['title'][:66]}")
    if result["false_positives"]:
        lines += ["", f"the {len(result['false_positives'])} positive(s) you would skip:"]
        for vid in result["false_positives"]:
            g = golden[vid]
            lines.append(f"  [{g['tool_verdict']:<11}] section 6: {str(g['applies']):<10} "
                         f"{g['title'][:62]}")
    if result["false_negatives"]:
        lines += ["", f"the {len(result['false_negatives'])} KEEP(s) the tool did not call "
                      f"positive:"]
        for vid in result["false_negatives"]:
            lines.append(f"  [{golden[vid]['tool_verdict']}] {golden[vid]['title'][:66]}")
    return "\n".join(lines)


def recorded(golden):
    """The labels' own recorded verdicts, as something score() can read: the before."""
    return {v: {"verdict": g["tool_verdict"], "listing_verdict": g["listing_verdict"],
                "applies": g["applies"]} for v, g in golden.items()}


# --- today's rules over the stored write-ups -------------------------------------------------
def labelled_record(core, g, history):
    """The record one labelled video implies, so it is scored through the same final_verdict
    the digest uses rather than around it. A video with no stored write-up keeps the state its
    history row implies, which is not written, so it can never be a positive."""
    row = history.get(g["id"]) or {}
    summary = row.get("summary")
    if not summary:
        record = core.record_from_history(row) if row else {
            "id": g["id"], "summary_state": "NOT_READ", "reason_code": "no_transcript",
            "state_reason": "no transcript and no summary stored for this video",
            "attempts": 0, "read_verdict": g["tool_verdict"]}
        record["id"] = g["id"]
        record["listing_verdict"] = g["listing_verdict"]
        record["read_verdict"] = g["tool_verdict"]
        return with_speech(core, record, g), []
    settled, _text, notes = core.settle_verdict(g["tool_verdict"], summary)
    applies, project, takeaway = core.section6_state(summary)
    return with_speech(core, {"id": g["id"], "summary_state": "WRITTEN", "reason_code": "written",
                              "state_reason": "", "attempts": 0, "terminal": False,
                              "listing_verdict": g["listing_verdict"],
                              "read_verdict": g["tool_verdict"], "settled_verdict": settled,
                              "applies": applies, "project": project, "takeaway": takeaway,
                              # The YES rule reads the write-up's own action, so the score
                              # carries section 8 and section 9 or it scores around the rule.
                              "try_this_week": core.extract_try_this_week(summary),
                              "header": core.writeup_header(summary),
                              # The day it was written, so the work label's cap of keeps a
                              # day applies per labelled day.
                              "written_day": g.get("date"), "day": g.get("date"),
                              "decision_hit": None}, g), notes


def with_speech(core, record, g):
    """The record with the no-speech check applied to the transcript on disk, so the score
    measures that rule rather than scoring around it."""
    raw = core.RAW_DIR / f"{g['id']}.txt"
    if not raw.exists():
        return record
    minutes, _source = core.measured_minutes(g["id"], record={})
    kind, words = core.speech_check(raw.read_text(encoding="utf-8", errors="replace"), minutes)
    if kind:
        record.update(no_speech=kind, words=words, minutes=minutes)
    return record


def rules_only(core, golden, history):
    """(rows, moved): today's rules over every labelled video, and the ones whose verdict
    moved, with why."""
    golden = list(golden.values()) if isinstance(golden, dict) else list(golden)
    rows, moved = [], []
    built = [labelled_record(core, g, history) for g in golden]
    core.apply_label_cap_to_days([record for record, _notes in built])
    for g, (record, notes) in zip(golden, built):
        after, state_text, _tags = core.final_verdict(record)
        if after != g["tool_verdict"]:
            moved.append((g, after, notes or ([state_text] if state_text else [])))
        rows.append({"id": g["id"], "verdict": after,
                     "listing_verdict": g["listing_verdict"], "applies": g["applies"]})
    return rows, moved


def rules_score(labels_path, core=None):
    """{yes_precision, yes_recall, projects_found, projects_total}: today's rules over the
    stored write-ups, scored against your labels, writing nothing. projects_found counts your
    keeps filed under one of your own projects that the rules still call positive."""
    if core is None:
        from . import core
    golden = load_labels(labels_path)
    history = history_by_id(core.HISTORY_PATH)
    rows, _moved = rules_only(core, golden, history)
    result = score(golden, {r["id"]: r for r in rows})
    verdicts = {r["id"]: r["verdict"] for r in rows}
    own = [p for p in core.LIVE_PROJECTS if p not in (core.SETUP_PROJECT, "this tool")]
    project_keeps = [g["id"] for g in golden.values() if g.get("your_verdict") == "KEEP"
                     and labelled_record(core, g, history)[0].get("project") in own]
    yes = result["by_verdict"]["YES"]
    return {"yes_precision": yes["precision"] or 0.0, "yes_recall": yes["recall"] or 0.0,
            "projects_found": sum(1 for v in project_keeps if verdicts.get(v) in POSITIVES),
            "projects_total": len(project_keeps)}
