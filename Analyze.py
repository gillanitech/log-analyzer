#!/usr/bin/env python3
"""
Log Analyzer — parse messy web server logs and produce a useful on-call report.
Usage: python analyze.py <logfile> [--top N] [--errors-only] [--json]
"""

import sys
import re
import json
import argparse
from collections import defaultdict, Counter
from datetime import datetime, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

TIMESTAMP_PATTERNS = [
    # ISO 8601  2024-03-15T14:23:01Z
    (re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)"), "%Y-%m-%dT%H:%M:%SZ"),
    # Slash date  2024/03/15 14:23:01
    (re.compile(r"^(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})"), "%Y/%m/%d %H:%M:%S"),
    # Day-Mon-Year  15-Mar-2024 14:23:01
    (re.compile(r"^(\d{2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2}:\d{2})"), "%d-%b-%Y %H:%M:%S"),
    # Unix epoch  1710512581
    (re.compile(r"^(\d{10})\b"), "epoch"),
]

RESPONSE_TIME_RE = re.compile(r"(\d+(?:\.\d+)?)(ms|s)?$", re.IGNORECASE)

STATUS_RE = re.compile(r"\b([1-5]\d{2})\b")

# Main log line pattern (flexible):
# <timestamp> <ip> <method> <path> <status> <response_time> [extra...]
MAIN_LOG_RE = re.compile(
    r"^(?P<ts>\S+(?:\s+\S+)?)\s+"           # timestamp (may have a space)
    r"(?P<ip>\d{1,3}(?:\.\d{1,3}){3})\s+"  # IPv4
    r"(?P<method>[A-Z]+)\s+"                 # HTTP method
    r"(?P<path>\S+)\s+"                      # path
    r"(?P<status>\d{3}|-)\s+"               # status code or -
    r"(?P<rt>\S+)"                           # response time
)


def parse_timestamp(raw: str) -> Optional[datetime]:
    raw = raw.strip()
    for pattern, fmt in TIMESTAMP_PATTERNS:
        m = pattern.match(raw)
        if m:
            val = m.group(1)
            if fmt == "epoch":
                try:
                    return datetime.fromtimestamp(int(val), tz=timezone.utc)
                except Exception:
                    return None
            else:
                # strip trailing Z for %S format
                val_clean = val.rstrip("Z")
                try:
                    return datetime.strptime(val_clean, fmt.rstrip("Z"))
                except Exception:
                    try:
                        return datetime.strptime(val, fmt)
                    except Exception:
                        return None
    return None


def parse_response_time_ms(raw: str) -> Optional[float]:
    """Normalise response time to milliseconds regardless of unit."""
    m = RESPONSE_TIME_RE.search(raw)
    if not m:
        return None
    value = float(m.group(1))
    unit = (m.group(2) or "ms").lower()
    if unit == "s":
        return value * 1000.0
    return value  # ms or bare number treated as ms


def try_parse_json_line(line: str) -> Optional[dict]:
    """Attempt to parse a JSON-formatted log line."""
    stripped = line.strip()
    if not stripped.startswith("{"):
        return None
    try:
        obj = json.loads(stripped)
        return obj
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class LogEntry:
    __slots__ = ("timestamp", "ip", "method", "path", "status", "response_ms", "raw", "source")

    def __init__(self, ts, ip, method, path, status, response_ms, raw, source="text"):
        self.timestamp = ts
        self.ip = ip
        self.method = method
        self.path = path
        self.status = status          # int or None
        self.response_ms = response_ms  # float or None
        self.raw = raw
        self.source = source          # "text" | "json"


# ---------------------------------------------------------------------------
# Line parser
# ---------------------------------------------------------------------------

def parse_line(line: str) -> tuple[Optional[LogEntry], str]:
    """
    Returns (LogEntry, "ok") on success, or (None, reason) on failure.
    Edge case: leading whitespace (e.g. indented continuation lines) is stripped
    before matching — without this, lines like '  2024-...' would always fail.
    """
    raw = line
    line = line.strip()

    if not line:
        return None, "blank"

    # --- JSON line? ---
    json_obj = try_parse_json_line(line)
    if json_obj is not None:
        # Try to extract fields from common JSON log schemas
        ts_raw = (json_obj.get("timestamp") or json_obj.get("time") or
                  json_obj.get("ts") or json_obj.get("@timestamp") or "")
        ip = (json_obj.get("ip") or json_obj.get("remote_addr") or
              json_obj.get("client") or "")
        method = (json_obj.get("method") or json_obj.get("http_method") or "")
        path = (json_obj.get("path") or json_obj.get("url") or
                json_obj.get("request") or "")
        status_raw = json_obj.get("status") or json_obj.get("status_code") or ""
        rt_raw = (str(json_obj.get("response_time") or json_obj.get("duration") or
                  json_obj.get("latency") or ""))

        ts = parse_timestamp(str(ts_raw)) if ts_raw else None
        try:
            status = int(status_raw) if status_raw and str(status_raw) != "-" else None
        except (ValueError, TypeError):
            status = None
        rt = parse_response_time_ms(rt_raw) if rt_raw else None

        if method and path:
            return LogEntry(ts, str(ip), str(method).upper(), str(path),
                            status, rt, raw, source="json"), "ok"
        return None, "json_missing_fields"

    # --- Standard/variant text line ---
    # Try the main regex first (handles leading spaces via line.strip() above)
    m = MAIN_LOG_RE.match(line)
    if m:
        ts = parse_timestamp(m.group("ts"))
        try:
            status = int(m.group("status")) if m.group("status") != "-" else None
        except ValueError:
            status = None
        rt = parse_response_time_ms(m.group("rt"))
        return LogEntry(ts, m.group("ip"), m.group("method").upper(),
                        m.group("path"), status, rt, raw), "ok"

    # --- Stack trace / continuation line? ---
    # These often start with whitespace, exception keywords, or "at "
    if re.match(r"^\s*(at |Exception|Error|Caused by|\.\.\.)", raw):
        return None, "stack_trace"

    return None, "malformed"


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze(entries: list[LogEntry], top_n: int = 10) -> dict:
    if not entries:
        return {}

    total = len(entries)
    status_counts: Counter = Counter()
    method_counts: Counter = Counter()
    path_counts: Counter = Counter()
    ip_counts: Counter = Counter()
    path_times: dict = defaultdict(list)
    error_paths: Counter = Counter()
    hourly: Counter = Counter()
    json_count = 0

    for e in entries:
        if e.source == "json":
            json_count += 1

        s = e.status
        status_counts[s if s is not None else "missing"] += 1
        method_counts[e.method] += 1
        path_counts[e.path] += 1
        if e.ip:
            ip_counts[e.ip] += 1
        if e.response_ms is not None:
            path_times[e.path].append(e.response_ms)
        if s is not None and s >= 400:
            error_paths[e.path] += 1
        if e.timestamp:
            hourly[e.timestamp.hour] += 1

    # Slow endpoints — sort by avg response time
    path_avg = {
        p: sum(times) / len(times)
        for p, times in path_times.items()
        if times
    }
    slowest = sorted(path_avg.items(), key=lambda x: x[1], reverse=True)[:top_n]

    # Error rate per path
    error_rate = {}
    for path, errs in error_paths.items():
        total_for_path = path_counts[path]
        error_rate[path] = (errs / total_for_path) * 100

    # Top error paths
    top_errors = sorted(error_paths.items(), key=lambda x: x[1], reverse=True)[:top_n]

    # Status breakdown
    status_2xx = sum(v for k, v in status_counts.items() if isinstance(k, int) and 200 <= k < 300)
    status_3xx = sum(v for k, v in status_counts.items() if isinstance(k, int) and 300 <= k < 400)
    status_4xx = sum(v for k, v in status_counts.items() if isinstance(k, int) and 400 <= k < 500)
    status_5xx = sum(v for k, v in status_counts.items() if isinstance(k, int) and 500 <= k < 600)
    status_missing = status_counts.get("missing", 0)

    # Response time stats (global)
    all_times = [e.response_ms for e in entries if e.response_ms is not None]
    rt_stats = {}
    if all_times:
        sorted_times = sorted(all_times)
        n = len(sorted_times)
        rt_stats = {
            "min_ms": round(sorted_times[0], 2),
            "max_ms": round(sorted_times[-1], 2),
            "avg_ms": round(sum(sorted_times) / n, 2),
            "p50_ms": round(sorted_times[int(n * 0.50)], 2),
            "p95_ms": round(sorted_times[int(n * 0.95)], 2),
            "p99_ms": round(sorted_times[int(n * 0.99)], 2),
        }

    # Peak hour
    peak_hour = max(hourly, key=hourly.get) if hourly else None

    return {
        "total_entries": total,
        "json_lines": json_count,
        "status_summary": {
            "2xx": status_2xx,
            "3xx": status_3xx,
            "4xx": status_4xx,
            "5xx": status_5xx,
            "missing": status_missing,
        },
        "method_counts": dict(method_counts.most_common()),
        "top_paths": dict(path_counts.most_common(top_n)),
        "top_ips": dict(ip_counts.most_common(top_n)),
        "slowest_endpoints": [
            {"path": p, "avg_ms": round(v, 2), "requests": len(path_times[p])}
            for p, v in slowest
        ],
        "top_error_paths": [
            {"path": p, "errors": c, "error_rate_pct": round(error_rate.get(p, 0), 1)}
            for p, c in top_errors
        ],
        "response_time_stats": rt_stats,
        "peak_hour": peak_hour,
        "hourly_distribution": dict(sorted(hourly.items())),
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def bar(value: int, max_value: int, width: int = 30) -> str:
    if max_value == 0:
        return ""
    filled = int((value / max_value) * width)
    return "█" * filled + "░" * (width - filled)


def print_report(stats: dict, skipped: dict, source_file: str, errors_only: bool = False) -> None:
    SEP = "─" * 60
    BOLD = "\033[1m"
    RED = "\033[91m"
    YEL = "\033[93m"
    GRN = "\033[92m"
    DIM = "\033[2m"
    RST = "\033[0m"

    def h(text):
        print(f"\n{BOLD}{text}{RST}")
        print(SEP)

    print(f"\n{BOLD}╔══ Log Analyzer Report ══╗{RST}")
    print(f"  File   : {source_file}")
    print(f"  Parsed : {stats['total_entries']:,} entries")
    print(f"  JSON   : {stats['json_lines']:,} lines")
    skipped_total = sum(skipped.values())
    print(f"  Skipped: {skipped_total:,} lines  " +
          "  ".join(f"{k}={v}" for k, v in skipped.items() if v))

    if not stats:
        print("\n  No valid entries found.\n")
        return

    # Status summary
    h("Status Codes")
    ss = stats["status_summary"]
    total = stats["total_entries"]
    for label, key, color in [
        ("2xx success", "2xx", GRN),
        ("3xx redirect", "3xx", DIM),
        ("4xx client err", "4xx", YEL),
        ("5xx server err", "5xx", RED),
        ("missing", "missing", DIM),
    ]:
        count = ss.get(key, 0)
        pct = (count / total * 100) if total else 0
        b = bar(count, total)
        print(f"  {color}{label:<16}{RST}  {b}  {count:>6,}  ({pct:.1f}%)")

    if not errors_only:
        # HTTP methods
        h("HTTP Methods")
        mc = stats["method_counts"]
        max_mc = max(mc.values()) if mc else 1
        for method, count in mc.items():
            print(f"  {method:<8}  {bar(count, max_mc, 20)}  {count:>6,}")

        # Top paths
        h("Top Endpoints")
        tp = stats["top_paths"]
        max_tp = max(tp.values()) if tp else 1
        for path, count in tp.items():
            print(f"  {count:>7,}  {bar(count, max_tp, 20)}  {path}")

    # Slowest endpoints
    h("Slowest Endpoints (avg response time)")
    se = stats["slowest_endpoints"]
    if se:
        max_rt = se[0]["avg_ms"] if se else 1
        for item in se:
            flag = f"{RED}SLOW{RST}" if item["avg_ms"] > 1000 else "    "
            print(f"  {flag}  {item['avg_ms']:>9.1f}ms  {bar(item['avg_ms'], max_rt, 20)}  "
                  f"{item['path']}  ({item['requests']} reqs)")
    else:
        print("  No response time data available.")

    # Error paths
    h("Top Error Paths (4xx + 5xx)")
    te = stats["top_error_paths"]
    if te:
        max_e = te[0]["errors"] if te else 1
        for item in te:
            color = RED if item["error_rate_pct"] > 50 else YEL
            print(f"  {color}{item['errors']:>6} errors{RST}  "
                  f"({item['error_rate_pct']:.1f}% err rate)  {item['path']}")
    else:
        print("  No errors found.")

    # Response time stats
    if stats["response_time_stats"]:
        h("Response Time Stats")
        rt = stats["response_time_stats"]
        print(f"  min    {rt['min_ms']:>10.1f} ms")
        print(f"  avg    {rt['avg_ms']:>10.1f} ms")
        print(f"  p50    {rt['p50_ms']:>10.1f} ms")
        print(f"  p95    {rt['p95_ms']:>10.1f} ms")
        print(f"  p99    {rt['p99_ms']:>10.1f} ms")
        print(f"  max    {rt['max_ms']:>10.1f} ms")

    if not errors_only:
        # Top IPs
        h("Top IP Addresses")
        ti = stats["top_ips"]
        max_ip = max(ti.values()) if ti else 1
        for ip, count in list(ti.items())[:5]:
            print(f"  {ip:<16}  {bar(count, max_ip, 20)}  {count:>6,}")

        # Hourly distribution
        if stats["hourly_distribution"]:
            h("Traffic by Hour (UTC)")
            hd = stats["hourly_distribution"]
            max_h = max(hd.values()) if hd else 1
            for hour in range(24):
                count = hd.get(hour, 0)
                if count:
                    print(f"  {hour:02d}:00  {bar(count, max_h, 25)}  {count:>6,}")

    peak = stats.get("peak_hour")
    if peak is not None:
        print(f"\n  Peak hour: {BOLD}{peak:02d}:00 UTC{RST}")

    print(f"\n{DIM}{'─' * 60}{RST}\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Analyze web server log files (handles messy / mixed formats)."
    )
    parser.add_argument("logfile", help="Path to the log file")
    parser.add_argument("--top", type=int, default=10,
                        help="Number of top results to show (default: 10)")
    parser.add_argument("--errors-only", action="store_true",
                        help="Show only error-related sections")
    parser.add_argument("--json", action="store_true",
                        help="Output raw JSON instead of human-readable report")
    args = parser.parse_args()

    entries: list[LogEntry] = []
    skipped: Counter = Counter()

    try:
        with open(args.logfile, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                entry, reason = parse_line(line)
                if entry is not None:
                    entries.append(entry)
                else:
                    skipped[reason] += 1
    except FileNotFoundError:
        print(f"Error: file not found: {args.logfile}", file=sys.stderr)
        sys.exit(1)
    except PermissionError:
        print(f"Error: permission denied: {args.logfile}", file=sys.stderr)
        sys.exit(1)

    stats = analyze(entries, top_n=args.top)

    if args.json:
        output = {
            "source": args.logfile,
            "parsed": len(entries),
            "skipped": dict(skipped),
            "stats": stats,
        }
        print(json.dumps(output, indent=2))
    else:
        print_report(stats, dict(skipped), args.logfile, errors_only=args.errors_only)


if __name__ == "__main__":
    main()
