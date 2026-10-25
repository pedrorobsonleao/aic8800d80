# AIC8800 performance comparison

## Test setup

- Date: 2026-10-25
- Host kernel: `7.3.0-9-generic` (x86_64)
- Adapter: AIC8800D80 USB, `a69c:8d81`
- Test interface: `wlx688fc9a24e57`
- `iperf3` server: `192.168.15.45` on the same LAN
- Benchmark: `tests/performance/benchmark_link.py`, 20 pings and 10 seconds
  of TCP throughput per run; three runs in each direction
- Current code revision: `9c1363d18ea46887bf98aedeae50e358994b078b`
- Requested baseline revision: `f9ab41822373efe70649d011edb587d1170364e7`

The currently installed DKMS source matches the repository's driver source
except for the KUnit-only parser-test additions (test object, test build
configuration, and test-symbol export).

## Current implementation results

| Direction | Throughput (Mbps), runs 1 / 2 / 3 | Median (Mbps) | Ping loss | Median ping RTT (ms) |
| --- | --- | ---: | ---: | ---: |
| Upload | 35.120 / 22.332 / 29.355 | 29.355 | 0% / 0% / 5% | 13.303 |
| Download | 45.296 / 55.673 / 43.301 | 45.296 | 0% / 0% / 0% | 19.459 |

Upload retransmissions were 411 / 28 / 102 (median 102). Download
retransmissions were not reported by `iperf3`. Ping showed occasional latency
spikes, so these measurements are indicative and should be compared with the
baseline under the same conditions.

Raw JSON for each run is in `current_upload_run*.json` and
`current_download_run*.json`.

## Baseline status

**Pending.** The baseline revision has not been installed or measured. This
shell has no non-interactive `sudo` authorization, so the driver cannot be
replaced here yet. Once authorized, install the baseline, repeat the same six
runs, save them as `baseline_upload_run*.json` and
`baseline_download_run*.json`, then add the median and percentage differences
to this report.
