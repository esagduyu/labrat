"""Taint-gate v2 structural layers (spec: docs/superpowers/specs/2026-07-16-
taint-gate-v2-design.md).

Inspects tool INPUTS structurally instead of substring-grepping trace text:
file-source classification for path-bearing arguments (``load_file``,
``attach_database``, and SQL file functions inside ``run_sql``/``check_sql``/
``explain_sql``), SQL string-literal folding (``||`` chains, ``concat(...)``,
``chr(N)``) so obfuscated needles and paths are seen assembled, and recursion
into composite tools (``run_program`` steps, ``subagent:``-prefixed names).

The rule set is layout-shape based and checkout-root independent: sanctioned
benchmark stores live under a ``query_dataset/`` segment, answer keys under
``query<N>/`` segments — a standalone run directory records no DAB checkout
path, so classification must not depend on one. Outputs are deliberately NOT
scanned here (benchmark row content legitimately contains URLs and
``validator``-ish strings); the v1 whole-text needle scan remains the last
layer in ``taint.py``.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass
from typing import Any, cast

from labrat.maze.scent_audit import detect_contamination

_SQL_TOOLS = {"run_sql", "check_sql", "explain_sql"}
_PATH_TOOLS = {"load_file", "attach_database"}

_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_DB_SCHEME_RE = re.compile(r"^(?:postgresql|postgres|mysql|mongodb)://", re.IGNORECASE)
_LOCAL_DB_URI_RE = re.compile(
    r"^(?:postgresql|postgres|mysql|mongodb)://(?:[^/@\s]*@)?"
    r"(?:localhost|127\.0\.0\.1|\[?::1\]?)?(?::\d+)?(?:/|$)",
    re.IGNORECASE,
)
_QUERY_DIR_RE = re.compile(r"(^|/)query_?\d+(/|$)", re.IGNORECASE)
# Generalized DuckDB file/table-function coverage: every read_* variant
# (read_csv[_auto], read_parquet, read_json[_auto], read_ndjson, read_text,
# read_blob, read_xlsx, ...), every *_scan alias (parquet_scan, sqlite_scan,
# postgres_scan, mysql_scan, delta_scan, iceberg_scan, ...), and glob().
_READ_FN_RE = re.compile(r"\b(?:read_[a-z0-9_]+|[a-z0-9_]+_scan|glob)\s*\(", re.IGNORECASE)
_ATTACH_RE = re.compile(r"\battach\b(?:\s+database\b)?", re.IGNORECASE)
_COPY_RE = re.compile(r"\bcopy\b", re.IGNORECASE)
_FROM_FILE_RE = re.compile(r"\bfrom\s+'((?:[^']|'')*)'", re.IGNORECASE)
_CHR_RE = re.compile(r"chr\s*\(\s*(\d+)\s*\)", re.IGNORECASE)
_CONCAT_OPEN_RE = re.compile(r"concat\s*\(", re.IGNORECASE)
_LIT_RE = re.compile(r"'((?:[^']|'')*)'")
_WS_RE = re.compile(r"\s+")
_GLUE_RE = re.compile(r"\|\|")

# File/dir names that are answer-shaped regardless of location. Substring match
# on the lowercased source string.
_SENSITIVE_SOURCE_NEEDLES = (
    "ground_truth",
    "ground truth",
    "ground-truth",
    "validate.py",
    "answer_key",
    "answer key",
    "gold answer",
    "expected_answer",
    "trials.jsonl",
    "submission.json",
    "taint.json",
    "mcp_tool_calls.jsonl",
    "agent_tool_calls.jsonl",
    "benchmark.json",
    "results.json",
    "queries.json",
)
_TEMP_MARKERS = ("/tmp/", "/var/folders/", "/private/tmp", "/private/var", "__trial")


@dataclass(frozen=True)
class TaintFinding:
    """One structural detection: which layer fired, on which event, and why."""

    layer: str  # "file_source" | "sql_literal"
    tag: str  # "answer_key" | "answer_key_dir" | "web_fetch" | "prior_artifact" | ...
    event_index: int
    tool: str
    detail: str


def _strip_sql_comments(sql: str) -> str:
    """Replace SQL comments with a single space, quote-aware.

    ``/* ... */`` (nested, as DuckDB/Postgres parse them) and ``-- ...`` to
    end-of-line are treated as whitespace so they cannot break literal folding
    (``concat('ground_', /*c*/ 'truth.csv')``). Comment markers inside
    single-quoted literals are preserved verbatim.
    """
    out: list[str] = []
    i = 0
    n = len(sql)
    in_literal = False
    while i < n:
        ch = sql[i]
        if in_literal:
            out.append(ch)
            if ch == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    out.append("'")
                    i += 1
                else:
                    in_literal = False
            i += 1
            continue
        if ch == "'":
            in_literal = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            depth = 1
            i += 2
            while i < n and depth:
                if sql.startswith("/*", i):
                    depth += 1
                    i += 2
                elif sql.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
            out.append(" ")
            continue
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            end = sql.find("\n", i + 2)
            i = n if end == -1 else end  # keep the newline itself
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def fold_sql_literals(sql: str) -> list[str]:
    """Extract single-quoted literals with concatenation folded.

    ``'a' || 'b'``, ``concat('a', 'b')`` (commas act as glue inside a concat
    call), and ``chr(N)`` all fold into one literal so obfuscated names are
    seen assembled. SQL comments are stripped (quote-aware) first and count as
    whitespace, never a fold-break. Any other token between literals breaks
    adjacency. Returns lowercased folded strings.
    """
    sql = _strip_sql_comments(sql)
    items: list[tuple[str, str]] = []  # (kind, text): LIT | GLUE | BREAK
    concat_depth: list[bool] = []  # per open paren: is this a concat frame?
    pos = 0
    n = len(sql)
    while pos < n:
        m = _WS_RE.match(sql, pos)
        if m:
            pos = m.end()
            continue
        m = _LIT_RE.match(sql, pos)
        if m:
            items.append(("LIT", m.group(1).replace("''", "'")))
            pos = m.end()
            continue
        m = _CHR_RE.match(sql, pos)
        if m:
            code = int(m.group(1))
            if 0 < code < 0x110000:
                items.append(("LIT", chr(code)))
            else:
                items.append(("BREAK", ""))
            pos = m.end()
            continue
        m = _GLUE_RE.match(sql, pos)
        if m:
            items.append(("GLUE", ""))
            pos = m.end()
            continue
        m = _CONCAT_OPEN_RE.match(sql, pos)
        if m:
            # Neutral (no break): a concat() call joins with whatever glue
            # precedes it, so nested concat chains fold through.
            concat_depth.append(True)
            pos = m.end()
            continue
        ch = sql[pos]
        if ch == "(":
            concat_depth.append(False)
            items.append(("BREAK", ""))
        elif ch == ")":
            in_concat = concat_depth.pop() if concat_depth else False
            # Closing a concat() keeps its folded value joinable with `||`.
            if not in_concat:
                items.append(("BREAK", ""))
        elif ch == ",":
            if concat_depth and concat_depth[-1]:
                items.append(("GLUE", ""))
            else:
                items.append(("BREAK", ""))
        else:
            items.append(("BREAK", ""))
        pos += 1

    folded: list[str] = []
    current: str | None = None
    pending_glue = False
    for kind, text in items:
        if kind == "LIT":
            if current is not None and pending_glue:
                current += text
            else:
                if current is not None:
                    folded.append(current)
                current = text
            pending_glue = False
        elif kind == "GLUE":
            pending_glue = True
        else:
            if current is not None:
                folded.append(current)
                current = None
            pending_glue = False
    if current is not None:
        folded.append(current)
    return [f.lower() for f in folded]


def classify_file_source(source: str) -> tuple[str, str] | None:
    """Classify one file-source string; return ``(tag, detail)`` or ``None``.

    Order is fail-closed severity: URL scheme, sensitive name, answer-key
    directory shape (``query<N>``), prior-run/VCS artifacts, traversal, and
    absolute paths outside the sanctioned ``query_dataset`` layout. Relative
    paths and temp/scratch locations are allowed.
    """
    s = source.strip().strip('"')
    if not s:
        return None
    low = s.lower()
    if _DB_SCHEME_RE.match(low):
        # Local database URIs are sanctioned-store shapes (the benchmark ships
        # Postgres/Mongo stores, e.g. patents' pg patent_CPCDefinition). A
        # remote host could serve answers, so only local hosts pass — subject
        # to the sensitive-name checks below.
        if not _LOCAL_DB_URI_RE.match(low):
            return ("web_fetch", f"remote database source: {s[:120]}")
        for needle in _SENSITIVE_SOURCE_NEEDLES:
            if needle in low:
                return ("answer_key", f"answer-shaped database source ({needle}): {s[:120]}")
        if _QUERY_DIR_RE.search(low):
            return ("answer_key_dir", f"benchmark query-dir database source: {s[:120]}")
        return None
    if _SCHEME_RE.match(low):
        if low.startswith("file://"):
            s = s[len("file://") :]
            low = low[len("file://") :]
        else:
            return ("web_fetch", f"URL file source: {s[:120]}")
    for needle in _SENSITIVE_SOURCE_NEEDLES:
        if needle in low:
            return ("answer_key", f"answer-shaped file source ({needle}): {s[:120]}")
    segments = [seg for seg in low.split("/") if seg]
    if ".." in segments:
        # Traversal is checked BEFORE the temp allow so `/tmp/x/../../...`
        # cannot launder an escape back into the checkout.
        return ("unsanctioned_path", f"path traversal in file source: {s[:120]}")
    if any(marker in low for marker in _TEMP_MARKERS):
        # Temp/scratch allow comes BEFORE the query-dir shape check:
        # agent-created exports under /tmp legitimately reuse task names like
        # "query3" (e.g. /tmp/query3/export.csv), and copying an actual answer
        # key into a temp dir would require a flagged read first. Answer-shaped
        # *names* (ground_truth.csv, ...) are still caught by the needle check
        # above regardless of location.
        return None
    if _QUERY_DIR_RE.search(low):
        return ("answer_key_dir", f"benchmark query-dir file source: {s[:120]}")
    if ".git" in segments or "leaderboard_submissions" in segments:
        return ("prior_artifact", f"repository/submission artifact source: {s[:120]}")
    for a, b in itertools.pairwise(segments):
        if a == "runs" and b == "dab":
            return ("prior_artifact", f"prior-run artifact source: {s[:120]}")
    if low.startswith("/") or low.startswith("~"):
        if "query_dataset" in segments:
            return None
        return ("unsanctioned_path", f"absolute path outside sanctioned layout: {s[:120]}")
    return None


def classify_folded_literal(literal: str) -> tuple[str, str] | None:
    """Spec §(b): layer-(a) classification for a *path-shaped* folded literal.

    Applies to any single-quoted SQL literal after folding — regardless of the
    surrounding function name — so unlisted/future DuckDB file functions fail
    closed. Path-shaped means: absolute (``/`` or ``~``), contains a ``..``
    segment, or carries a URL scheme. Plain relative strings never classify.

    URL-scheme literals get the *answer-shape* checks only (sensitive needles,
    ``query<N>`` dir segments, remote/DB schemes, ``file://``) rather than the
    blanket ``web_fetch`` tag: URLs as SQL filter values are a real benign
    corpus shape, while a URL in a genuine file-source position is already
    tagged ``web_fetch`` by layer (a).
    """
    s = literal.strip().strip('"')
    if not s:
        return None
    low = s.lower()
    if _DB_SCHEME_RE.match(low) or low.startswith("file://"):
        return classify_file_source(s)
    if _SCHEME_RE.match(low):
        for needle in _SENSITIVE_SOURCE_NEEDLES:
            if needle in low:
                return ("answer_key", f"answer-shaped URL literal ({needle}): {s[:120]}")
        if _QUERY_DIR_RE.search(low):
            return ("answer_key_dir", f"benchmark query-dir URL literal: {s[:120]}")
        return None
    segments = [seg for seg in low.split("/") if seg]
    if not segments:
        # Slash-only literals ('/', '//') are string delimiters in real corpus
        # SQL (split_part(name, '/', 1)), not paths.
        return None
    if low.startswith("/") or low.startswith("~") or ".." in segments:
        return classify_file_source(s)
    return None


def _matching_paren_slice(text: str, open_pos: int, limit: int = 2000) -> str:
    """Return the argument slice from just after ``(`` to its matching ``)``."""
    depth = 1
    i = open_pos + 1
    end = min(len(text), open_pos + limit)
    in_literal = False
    while i < end:
        ch = text[i]
        if in_literal:
            if ch == "'":
                if i + 1 < end and text[i + 1] == "'":
                    i += 1
                else:
                    in_literal = False
        elif ch == "'":
            in_literal = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[open_pos + 1 : i]
        i += 1
    return text[open_pos + 1 : end]


def extract_sql_file_sources(sql: str) -> list[str]:
    """Folded literals appearing in file-source positions of a SQL string."""
    sql = _strip_sql_comments(sql)
    sources: list[str] = []
    for m in _READ_FN_RE.finditer(sql):
        sources.extend(fold_sql_literals(_matching_paren_slice(sql, m.end() - 1)))
    for m in _ATTACH_RE.finditer(sql):
        tail = sql[m.end() : m.end() + 300].split(";", 1)[0]
        folded = fold_sql_literals(tail)
        if folded:
            sources.append(folded[0])
    for m in _COPY_RE.finditer(sql):
        tail = sql[m.end() : m.end() + 500].split(";", 1)[0]
        for fm in re.finditer(r"(?i)\b(?:from|to)\s+'((?:[^']|'')*)'", tail):
            sources.append(fm.group(1).replace("''", "'").lower())
    for m in _FROM_FILE_RE.finditer(sql):
        sources.append(m.group(1).replace("''", "'").lower())
    return sources


def _base_tool(tool: str) -> str:
    return tool.split(":", 1)[1] if tool.startswith("subagent:") else tool


def _expand_inputs(tool: str, tool_input: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Yield (tool_label, input) for an event, expanding run_program steps."""
    base = _base_tool(tool)
    expanded: list[tuple[str, dict[str, Any]]] = [(tool, tool_input)]
    if base == "run_program":
        steps = tool_input.get("steps")
        if isinstance(steps, list):
            for raw_step in cast(list[Any], steps):
                if not isinstance(raw_step, dict):
                    continue
                step = cast(dict[str, Any], raw_step)
                step_tool = step.get("tool")
                step_args = step.get("args")
                if isinstance(step_tool, str) and isinstance(step_args, dict):
                    expanded.append((f"{tool}:{step_tool}", cast(dict[str, Any], step_args)))
    return expanded


def scan_records(records: list[dict[str, Any]]) -> list[TaintFinding]:
    """Run the structural layers over parsed trace records (inputs only)."""
    findings: list[TaintFinding] = []
    for index, record in enumerate(records):
        tool = str(record.get("tool") or "")
        raw_input = record.get("input")
        if not isinstance(raw_input, dict):
            continue
        for label, tool_input in _expand_inputs(tool, cast(dict[str, Any], raw_input)):
            base = _base_tool(label).rsplit(":", 1)[-1]
            if base in _PATH_TOOLS:
                path = tool_input.get("path")
                if isinstance(path, str):
                    hit = classify_file_source(path)
                    if hit is not None:
                        findings.append(TaintFinding("file_source", hit[0], index, label, hit[1]))
            elif base in _SQL_TOOLS:
                sql = tool_input.get("query")
                if not isinstance(sql, str):
                    sql = tool_input.get("sql") if isinstance(tool_input.get("sql"), str) else ""
                sql = cast(str, sql)
                if not sql:
                    continue
                for source in extract_sql_file_sources(sql):
                    hit = classify_file_source(source)
                    if hit is not None:
                        findings.append(TaintFinding("file_source", hit[0], index, label, hit[1]))
                for literal in fold_sql_literals(sql):
                    tag = detect_contamination(literal)
                    if tag is not None:
                        findings.append(
                            TaintFinding(
                                "sql_literal",
                                tag,
                                index,
                                label,
                                f"folded SQL literal matches needle: {literal[:120]}",
                            )
                        )
                        continue
                    hit = classify_folded_literal(literal)
                    if hit is not None:
                        findings.append(
                            TaintFinding(
                                "sql_literal",
                                hit[0],
                                index,
                                label,
                                f"path-shaped folded SQL literal: {hit[1]}",
                            )
                        )
    return findings
