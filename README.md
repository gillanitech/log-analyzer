# Log Analyzer

A CLI tool that parses messy web server log files and produces an on-call–friendly report: status code breakdown, slowest endpoints, error hotspots, traffic by hour, and response time percentiles.

Handles real-world log noise gracefully — mixed timestamp formats, response times in ms/s/bare numbers, missing status codes, JSON-formatted lines, stack traces, blank lines, and fully malformed entries — without crashing.

---

## Quick start (fresh machine)

**Requirements:** Python 3.8 or later. No third-party packages needed — uses the standard library only.

```bash
git clone <repo-url>
cd log-analyzer

# Analyze a log file
python3 analyze.py /path/to/your/server.log

# Show only the top 5 results per section
python3 analyze.py /path/to/your/server.log --top 5

# Show only error-related sections (useful when you're on call)
python3 analyze.py /path/to/your/server.log --errors-only

# Output raw JSON (pipe into jq, save to file, etc.)
python3 analyze.py /path/to/your/server.log --json
```

---

## Generating test data

A log generator is included in `scripts/`. It produces a file that matches the shape described in the assessment brief, including all the deviations (mixed timestamp formats, JSON lines, stack traces, malformed lines, leading whitespace, missing status codes, varied response time units).

```bash
# Default: 5000 lines → sample.log
python3 scripts/generate_logs.py

# Custom size and output path
python3 scripts/generate_logs.py --lines 50000 --output big_test.log

# Then analyze it
python3 analyze.py sample.log
```

---

## What the report shows

| Section | Why it matters on call |
|---|---|
| Status code breakdown | Instant sense of error rate |
| HTTP method distribution | Spot unexpected verb spikes |
| Top endpoints | Volume — where the traffic is |
| Slowest endpoints (avg ms) | Latency problems, SLO violations |
| Top error paths + error rate % | Which endpoint is on fire |
| Response time stats (p50/p95/p99) | Tail latency hidden by averages |
| Top IPs | Detect abuse / runaway client |
| Traffic by hour | Spot anomalies vs. normal peak |

---

## Log formats understood

| Format | Example |
|---|---|
| ISO 8601 (default) | `2024-03-15T14:23:01Z` |
| Slash date | `2024/03/15 14:23:01` |
| Day-Mon-Year | `15-Mar-2024 14:23:01` |
| Unix epoch | `1710512581` |
| Response time ms | `142ms` |
| Response time s | `0.142s` |
| Response time bare | `142` |
| Status code missing | `-` |
| JSON lines | `{"timestamp":…, "method":…}` |
| Extra fields | lines with trailing user-agent or referrer strings |

Malformed lines, blank lines, and stack traces are silently counted and reported in the header — nothing is dropped without being accounted for.

---

## Options

```
positional arguments:
  logfile        Path to the log file

options:
  --top N        Number of top results per section (default: 10)
  --errors-only  Show only status codes, slowest endpoints, error paths
  --json         Machine-readable JSON output instead of the terminal report
  -h, --help     Show this message
```
