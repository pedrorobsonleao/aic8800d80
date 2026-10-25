# Wi-Fi and Bluetooth link performance check

`benchmark_link.py` measures ICMP packet loss and round-trip latency, then TCP
throughput using `iperf3`. It does not change network settings or install
anything. Results are JSON so repeated runs can be compared.

## Requirements

- `ping` (usually provided by `iputils-ping`)
- `iperf3` on the test computer
- An `iperf3` server on a reachable second computer

On the second computer, start:

```sh
iperf3 -s
```

Use that computer's address as the target. For a local Wi-Fi link, place the
server on the same LAN. This isolates the wireless link from internet speed
limits. To include the internet path, use a remote `iperf3` server you control.

## Wi-Fi run

Find the Wi-Fi interface with `iw dev`, then run both directions:

```sh
python3 tests/performance/benchmark_link.py 192.168.1.20 --interface wlx688fc9a24e57
python3 tests/performance/benchmark_link.py 192.168.1.20 --interface wlx688fc9a24e57 --reverse
```

The output reports packet loss, min/average/max RTT, jitter, TCP throughput in
Mbps, and TCP retransmissions when iperf3 provides them. Repeat each direction
three times and compare the median to reduce variation from transient traffic.

## Bluetooth PAN run

Bluetooth performance is measured only when Bluetooth PAN networking is
connected and has an IP route to the iperf3 server. Find the PAN interface and
its local IP, then bind the traffic to it:

```sh
python3 tests/performance/benchmark_link.py 192.168.44.2 \
  --interface bnep0 --bind 192.168.44.1
python3 tests/performance/benchmark_link.py 192.168.44.2 \
  --interface bnep0 --bind 192.168.44.1 --reverse
```

This measures Bluetooth PAN networking. It does not measure BLE GATT, audio,
or raw Bluetooth radio throughput; those require a peer and workload specific
to the Bluetooth profile being evaluated.

## Interpreting results

Run Wi-Fi and Bluetooth PAN separately against the same server, at similar
times and distances. Nonzero packet loss, high or unstable RTT, or throughput
that changes substantially between runs can point to interference, weak signal,
congestion, or server limits. The script reports measurements without imposing
one pass/fail threshold because expected rates depend on the adapter, band,
distance, channel width, Bluetooth profile, and server.
