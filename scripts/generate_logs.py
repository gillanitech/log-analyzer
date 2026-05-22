#!/usr/bin/env python3
"""
Generate a representative web server log file with realistic noise.
Usage: python scripts/generate_logs.py [--lines N] [--output PATH]
"""

import random
import json
import argparse
from datetime import datetime, timedelta, timezone

METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
PATHS = [
    "/api/users", "/api/users/12", "/api/users/99", "/api/login",
    "/api/logout", "/api/orders", "/api/orders/456", "/api/products",
    "/api/products/7", "/health", "/metrics", "/static/app.js",
    "/static/style.css", "/api/search", "/api/admin/reports",
    "/api/auth/refresh", "/api/notifications", "/api/upload",
]
STATUS_CODES = (
    [200] * 60 + [201] * 10 + [204] * 5 +
    [301] * 3 + [304] * 5 +
    [400] * 5 + [401] * 8 + [403] * 3 + [404] * 6 + [429] * 2 +
    [500] * 4 + [502] * 2 + [503] * 1
)
IPS = [
    "192.168.1.42", "10.0.0.7", "10.0.0.8", "172.16.0.5",
    "203.0.113.9", "198.51.100.14", "192.168.1.1", "10.0.1.55",
]
USER_AGENTS = [
    '"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"',
    '"curl/7.68.0"',
    '"python-requests/2.28.0"',
    '"Go-http-client/1.1"',
]

STACK_TRACE_LINES = [
    "Exception in thread 'main' java.lang.NullPointerException",
    "\tat com.example.Service.handleRequest(Service.java:42)",
    "\tat com.example.Server.process(Server.java:118)",
    "Caused by: java.io.IOException: Broken pipe",
]


def fmt_timestamp_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_timestamp_slash(dt: datetime) -> str:
    return dt.strftime("%Y/%m/%d %H:%M:%S")


def fmt_timestamp_dmy(dt: datetime) -> str:
    return dt.strftime("%d-%b-%Y %H:%M:%S")


def fmt_timestamp_epoch(dt: datetime) -> str:
    return str(int(dt.timestamp()))


def fmt_response_time(ms: float, variant: int) -> str:
    if variant == 0:
        return f"{int(ms)}ms"
    elif variant == 1:
        return f"{ms/1000:.3f}s"
    else:
        return str(int(ms))


def generate_log_line(dt: datetime) -> str:
    ip = random.choice(IPS)
    method = random.choices(METHODS, weights=[50, 20, 10, 8, 5, 7])[0]
    path = random.choice(PATHS)
    status = random.choice(STATUS_CODES)
    # Slower response for errors and certain endpoints
    base_ms = random.lognormvariate(4.5, 0.8)
    if status >= 500:
        base_ms *= 3
    if "upload" in path or "reports" in path:
        base_ms *= 5

    # Choose timestamp format
    ts_variant = random.choices([0, 1, 2, 3], weights=[70, 10, 10, 10])[0]
    ts_funcs = [fmt_timestamp_iso, fmt_timestamp_slash, fmt_timestamp_dmy, fmt_timestamp_epoch]
    ts = ts_funcs[ts_variant](dt)

    # Choose response time format
    rt_variant = random.choices([0, 1, 2], weights=[75, 15, 10])[0]
    rt = fmt_response_time(base_ms, rt_variant)

    # Occasionally drop status code
    status_str = str(status) if random.random() > 0.03 else "-"

    # Occasionally add extra fields (user agent, referrer)
    extra = ""
    if random.random() < 0.15:
        extra = " " + random.choice(USER_AGENTS)

    # Occasionally add leading whitespace (indented line)
    indent = "  " if random.random() < 0.05 else ""

    return f"{indent}{ts} {ip} {method} {path} {status_str} {rt}{extra}"


def generate_json_line(dt: datetime) -> str:
    ip = random.choice(IPS)
    method = random.choices(METHODS, weights=[50, 20, 10, 8, 5, 7])[0]
    path = random.choice(PATHS)
    status = random.choice(STATUS_CODES)
    duration_ms = random.lognormvariate(4.5, 0.8)

    obj = {
        "timestamp": fmt_timestamp_iso(dt),
        "remote_addr": ip,
        "method": method,
        "path": path,
        "status": status,
        "duration": f"{int(duration_ms)}ms",
        "service": "api-gateway",
    }
    return json.dumps(obj)


def generate_log_file(n_lines: int, output_path: str) -> None:
    start = datetime(2024, 3, 15, 0, 0, 0, tzinfo=timezone.utc)
    # Simulate a day's worth of traffic with a rush-hour spike
    lines = []
    dt = start

    for i in range(n_lines):
        # Advance time — shorter gaps during "peak hours" 9-11 and 14-16
        hour = dt.hour
        if 9 <= hour <= 11 or 14 <= hour <= 16:
            gap = random.expovariate(5)   # faster — ~0.2s avg
        else:
            gap = random.expovariate(0.5)  # slower — ~2s avg
        dt += timedelta(seconds=gap)
        if dt.day > start.day:
            dt = start + timedelta(seconds=1)  # wrap back for demo

        roll = random.random()

        if roll < 0.05:
            # Blank line
            lines.append("")
        elif roll < 0.07:
            # Stack trace burst (1-3 lines)
            for sl in random.sample(STACK_TRACE_LINES, k=random.randint(1, 3)):
                lines.append(sl)
        elif roll < 0.10:
            # Partial / fully malformed line
            junk_options = [
                f"PARTIAL {dt.strftime('%H:%M:%S')} truncated",
                "???",
                "binary\x00data\xff here",
                f"[ERROR] {dt.isoformat()} something went wrong without structure",
            ]
            lines.append(random.choice(junk_options))
        elif roll < 0.17:
            # JSON-formatted line (different logging config)
            lines.append(generate_json_line(dt))
        else:
            lines.append(generate_log_line(dt))

    with open(output_path, "w", encoding="utf-8", errors="replace") as fh:
        fh.write("\n".join(lines) + "\n")

    print(f"Generated {len(lines)} lines → {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate a test web server log file.")
    parser.add_argument("--lines", type=int, default=5000,
                        help="Number of log lines to generate (default: 5000)")
    parser.add_argument("--output", default="sample.log",
                        help="Output file path (default: sample.log)")
    args = parser.parse_args()
    generate_log_file(args.lines, args.output)


if __name__ == "__main__":
    main()
