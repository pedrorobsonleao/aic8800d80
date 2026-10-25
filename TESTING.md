# Testing and performance

This guide covers the repository's automated checks, kernel builds, optional
KUnit tests, and Wi-Fi/Bluetooth link-performance measurements. Run the
commands from the repository root unless a section says otherwise.

## Source and installer regression checks

These checks do not require an adapter, root privileges, or kernel headers:

```sh
python3 -m unittest discover -s tests/driver-safety -p 'test_*.py'
python3 tests/installer/test_install.py
```

The driver-safety suite checks source-level contracts for safety fixes. The
installer suite tests selected installer functions with mocked system commands
and temporary firmware. Neither suite executes kernel code or validates
hardware behavior.

## Build the kernel modules

Install the development headers that match the kernel you are building for,
then compile all three modules:

```sh
make -C drivers/aic8800
```

The default build uses the running kernel's build tree under
`/lib/modules/$(uname -r)/build`. To select a different prepared kernel build
tree, pass `KVER` and `KDIR`, for example:

```sh
make -C drivers/aic8800 KVER=6.12.94 KDIR=/path/to/linux-6.12.94
```

A successful build verifies compilation and module linking only. It does not
validate firmware loading, connectivity, or runtime behavior. For OpenWrt
backports, use the dedicated
[OpenWrt build regression procedure](tests/openwrt-backports/README.md).

## KUnit RX parser tests

The KUnit suite exercises the production RX transport-length validator with
synthetic malformed frames. It requires a test kernel configured with
`CONFIG_KUNIT=y`; KASAN and UBSAN can add memory-safety coverage. Build the
driver and test module against that kernel:

```sh
make -C /path/to/test-kernel/build \
  M="$PWD/drivers/aic8800/aic8800_fdrv" \
  CONFIG_AICWF_RX_KUNIT_TEST=m CONFIG_USB_SUPPORT=y CONFIG_SDIO_SUPPORT=n \
  CONFIG_PREALLOC_RX_SKB=y CONFIG_PREALLOC_TXQ=y modules
```

Boot the same test kernel and load the matching `aic8800_fdrv` module first;
the KUnit module calls a symbol exported by that driver build. Then load the
test module and check the result in the kernel log or
`/sys/kernel/debug/kunit/`:

```sh
sudo insmod "$PWD/drivers/aic8800/aic8800_fdrv/aicwf_rx_kunit.ko"
sudo dmesg | grep 'aicwf-rx-parser'
```

These tests do not send frames to the adapter or firmware and do not cover every
firmware-message callback. See the
[driver-safety test notes](tests/driver-safety/README.md) for details and
limitations.

## Wi-Fi and Bluetooth PAN performance

Performance measurements require `ping`, `iperf3`, a working link, and a
reachable second computer running an `iperf3` server. On the server:

```sh
iperf3 -s
```

On the test computer, find the Wi-Fi interface and its local IP address with
`iw dev` and `ip -4 addr show dev <interface>`. Substitute the server address,
interface name, and local IP address, then measure upload and download
separately. `--interface` selects the interface for ping; `--bind` keeps the
TCP test on that link:

```sh
python3 tests/performance/benchmark_link.py 192.168.1.20 \
  --interface wlx688fc9a24e57 --bind 192.168.1.10
python3 tests/performance/benchmark_link.py 192.168.1.20 \
  --interface wlx688fc9a24e57 --bind 192.168.1.10 --reverse
```

For Bluetooth PAN, first establish the PAN connection and route to the server.
Use the PAN interface and its local IP address:

```sh
python3 tests/performance/benchmark_link.py 192.168.44.2 \
  --interface bnep0 --bind 192.168.44.1
python3 tests/performance/benchmark_link.py 192.168.44.2 \
  --interface bnep0 --bind 192.168.44.1 --reverse
```

The script prints JSON containing packet loss, round-trip latency, jitter, TCP
throughput, and retransmissions when available. Repeat each direction at least
three times and compare the median under similar conditions. These are
measurements, not pass/fail tests: results depend on the adapter, signal,
interference, server, and network path. Bluetooth PAN results do not measure
BLE, audio, or raw Bluetooth radio throughput. See the
[performance test notes](tests/performance/README.md) for setup and
interpretation details.

## Hardware and runtime validation

Before release, build against each relevant target kernel and exercise the
affected USB and SDIO paths on suitable test hardware, including disconnect
and reconnect. Use KASAN, lockdep, and UBSAN when available. Record the kernel,
architecture, adapter/chip revision, firmware, configuration, commands, and
results. A source check, successful build, KUnit run, or performance result
alone does not establish complete runtime safety or compatibility.
