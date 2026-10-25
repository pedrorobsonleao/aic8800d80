#!/usr/bin/env python3
"""Measure latency, packet loss, and TCP throughput over a chosen network link.

Requires ping and iperf3. The target must run an iperf3 server:
    iperf3 -s

Use a Wi-Fi gateway/host for Wi-Fi measurements. For Bluetooth, first establish
a Bluetooth PAN connection and pass its network interface and local address.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys


def run(command, timeout):
    return subprocess.run(
        command, check=False, capture_output=True, text=True, timeout=timeout
    )


def ping_metrics(output):
    result = {}
    loss = re.search(r"([\d.]+)% packet loss", output)
    if loss:
        result["packet_loss_percent"] = float(loss.group(1))
    rtt = re.search(
        r"(?:rtt|round-trip) min/avg/max/(?:mdev|stddev) = "
        r"([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms",
        output,
    )
    if rtt:
        result.update(
            rtt_min_ms=float(rtt.group(1)),
            rtt_avg_ms=float(rtt.group(2)),
            rtt_max_ms=float(rtt.group(3)),
            rtt_jitter_ms=float(rtt.group(4)),
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="IP/hostname of the ping target and iperf3 server")
    parser.add_argument("--interface", help="Interface to use for ping, e.g. wlx... or bnep0")
    parser.add_argument("--bind", help="Local IP on that link (binds iperf3 to this address)")
    parser.add_argument("--count", type=int, default=20, help="Ping packet count (default: 20)")
    parser.add_argument("--seconds", type=int, default=10, help="iperf3 duration (default: 10)")
    parser.add_argument("--reverse", action="store_true", help="Measure download direction")
    args = parser.parse_args()

    if args.count < 1 or args.seconds < 1:
        parser.error("--count and --seconds must be positive")
    missing = [tool for tool in ("ping", "iperf3") if not shutil.which(tool)]
    if missing:
        print("Missing required command(s): " + ", ".join(missing), file=sys.stderr)
        return 2

    report = {"target": args.host, "interface": args.interface, "ping": {}, "iperf3": {}}
    ping_command = ["ping", "-n", "-c", str(args.count), "-W", "2"]
    if args.interface:
        ping_command += ["-I", args.interface]
    ping_command.append(args.host)
    try:
        ping = run(ping_command, timeout=args.count * 3 + 5)
        report["ping"] = ping_metrics(ping.stdout + "\n" + ping.stderr)
        report["ping"]["exit_code"] = ping.returncode
        if not report["ping"].get("packet_loss_percent"):
            report["ping"]["details"] = (ping.stdout + ping.stderr).strip()[-1000:]
    except subprocess.TimeoutExpired:
        report["ping"] = {"error": "ping timed out"}

    iperf_command = ["iperf3", "-c", args.host, "-t", str(args.seconds), "-J"]
    if args.bind:
        iperf_command += ["-B", args.bind]
    if args.reverse:
        iperf_command.append("-R")
    try:
        iperf = run(iperf_command, timeout=args.seconds + 15)
        if iperf.stdout:
            try:
                data = json.loads(iperf.stdout)
                end = data.get("end", {})
                direction = "sum_received" if args.reverse else "sum_sent"
                summary = end.get(direction) or end.get("sum_received") or end.get("sum_sent") or {}
                report["iperf3"] = {
                    "direction": "download" if args.reverse else "upload",
                    "throughput_mbps": round(summary.get("bits_per_second", 0) / 1_000_000, 3),
                    "retransmits": summary.get("retransmits"),
                    "exit_code": iperf.returncode,
                }
                if data.get("error"):
                    report["iperf3"]["error"] = data["error"]
            except (json.JSONDecodeError, AttributeError):
                report["iperf3"] = {"error": (iperf.stderr or iperf.stdout).strip()[-1000:]}
        else:
            report["iperf3"] = {"error": iperf.stderr.strip()[-1000:] or "no iperf3 output"}
    except subprocess.TimeoutExpired:
        report["iperf3"] = {"error": "iperf3 timed out"}

    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report["iperf3"].get("exit_code", 1) != 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
