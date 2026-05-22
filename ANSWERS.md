# ANSWERS.md

## 1. How to run

Requirements: Python 3.8+. No pip installs needed.

```bash
# Generate test data first
python3 scripts/generate_logs.py --lines 5000 --output sample.log

# Run the analyzer
python3 analyze.py sample.log

# Useful flags
python3 analyze.py sample.log --top 5          # fewer rows
python3 analyze.py sample.log --errors-only    # on-call mode
python3 analyze.py sample.log --json           # machine-readable output
```

That's it. No virtual environment, no `.env`, no build step.

---

## 2. Stack choice

**Why Python 3 stdlib CLI:**

The task is text parsing and aggregation — exactly what Python excels at. The standard library covers everything needed (`re`, `json`, `collections.Counter`, `datetime`, `argparse`), so a reviewer can run it on a fresh machine without a single `pip install`. Python's regex engine handles multi-pattern timestamp matching cleanly, and the REPL makes iterative testing of edge-case inputs fast.

The output is a terminal report because that's what someone actually reaching for during an incident wants — not a browser tab to open.

**A worse choice: Node.js**

Not because Node is bad, but because a synchronous line-by-line parser in Node means either going full async (overkill for a file read) or using `readline` in a way that's more ceremonial than the Python equivalent. Packaging it so a reviewer can run it without `npm install` or a specific Node version is harder. For a CLI parsing task with no I/O concurrency benefit, the extra ceremony adds cost without benefit.

**Another worse choice: a full web app framework (Flask, Express, etc.)**

The task is a file analysis tool. Wrapping it in an HTTP server means the reviewer has to start a server, open a browser, and upload a file just to see a report. That's friction that adds nothing for an off-call analyst or a CI pipeline.

---

## 3. One real edge case

**Leading whitespace on otherwise-valid log lines.**

File: `analyze.py`, line 102–109.

```python
def parse_line(line: str) -> tuple[Optional[LogEntry], str]:
    raw = line
    line = line.strip()          # ← here
    ...
    m = MAIN_LOG_RE.match(line)  # matching against stripped line
```

The log format described in the brief shows an example with leading spaces:

```
  2024-03-15T14:23:04Z 192.168.1.42 GET /api/users/12 200 53ms
```

Without `.strip()` before the regex match, `MAIN_LOG_RE` (which is anchored at `^`) would fail to match because the line starts with two spaces rather than a timestamp character. The line would be counted as `malformed` and silently dropped from all statistics.

With the strip, the line parses correctly. The `raw` variable preserves the original line for any future raw-output needs, while `line` is the cleaned version used for matching.

The generator (`scripts/generate_logs.py`, line 94) reproduces this: roughly 5% of generated lines have leading whitespace to exercise this path during development.

---

## 4. AI usage

**Tool used:** Claude (claude.ai)

### Where AI was used:

**a) Timestamp pattern list**
Asked: "What are all the common timestamp formats found in web server logs, give me regex patterns for each."
Got: A list of 6 patterns including RFC 2822 and Apache combined log format.
**What I changed:** Dropped RFC 2822 and Apache combined format. The brief only hints at ISO, slash, Day-Mon-Year, and Unix epoch — adding more patterns made the code longer without being tested against real input. I also rewrote the patterns to match from the start of the string rather than anywhere in the line, to avoid false positives when a timestamp-like string appears in a URL.

**b) JSON field name variants**
Asked: "What are common field names used for IP, method, path, status, and response time in JSON-formatted access logs?"
Got: A table with about 12 variants per field.
**What I changed:** Trimmed the list to the ~3–4 most common variants per field. The AI output was correct but exhaustive in a way that would have made the code unreadable. Real JSON log schemas (nginx, AWS ALB, structured loggers) converge on a handful of names; the long tail adds noise.

**c) ASCII bar chart rendering**
Asked: "How can I render a simple horizontal bar chart in a terminal using only ASCII characters?"
Got: A version using `#` for fill and spaces for empty.
**What I changed:** Switched to `█` and `░` (Unicode block elements). They render cleanly in any modern terminal that supports UTF-8, which is every terminal in 2024, and they look significantly better than `#` without adding any dependency.

---

## 5. Honest gap

**The response time normalisation for bare numbers is a guess.**

In `analyze.py`, `parse_response_time_ms()` (line 61): when a line has a bare number with no unit (e.g. `142` instead of `142ms` or `0.142s`), the code treats it as milliseconds.

```python
return value  # ms or bare number treated as ms
```

This is the most common convention, but it's an assumption. Some services log in microseconds, some in nanoseconds. If a log file uses microseconds for bare values, every response time will be reported as 1000× too large — the p99 might show `1,200,000 ms` when the real value is `1,200 ms`.

**To fix it with another day:** Implement a heuristic: parse a sample of bare-number lines, compute the distribution, and if the median is above ~10,000 (plausible ms) but all values cluster in the millions, infer microseconds and scale accordingly. Then surface a warning in the report header: `"Note: response times appeared to be in microseconds — normalised to ms."` This is the kind of inference a human analyst would do instinctively; the code should do it too.
