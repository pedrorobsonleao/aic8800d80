# Driver safety regression checks

Run the source-contract checks from the repository root:

```bash
python3 -m unittest discover -s tests/driver-safety -p 'test_*.py'
```

They cover the fixes for TX SKB ownership, AMSDU bounds, USB probe cleanup,
command-buffer size, TCP ACK allocation failure, and RX reorder TID bounds.
They also check that typed firmware-confirmation buffers carry their capacities
and oversized responses fail before reaching callbacks or copies.
They need no hardware or root privileges and fail if the guarded source
contracts are removed or reordered.

These are structural checks of the C source. They do not execute kernel code or
prove memory safety at runtime. Before release, also build against each target
kernel; exercise USB and SDIO on hardware, including malformed/reordered RX
traffic and disconnect/reconnect; and use KASAN/lockdep where available.

## KUnit RX parser tests

`aicwf_rx_kunit.c` exercises the production transport-length validator with
synthetic malformed USB RX frames: truncated headers/data, invalid command
response lengths, oversized IPC parameters, and undersized TX confirmations.
The tests do not transmit anything to the adapter or firmware.

Run them in a test kernel built with `CONFIG_KUNIT=y`; enable KASAN and UBSAN
there as well when checking memory-safety paths. Build the driver and test
module against that kernel, from the repository root:

```bash
make -C /lib/modules/$(uname -r)/build \
  M="$PWD/drivers/aic8800/aic8800_fdrv" \
  CONFIG_AICWF_RX_KUNIT_TEST=m CONFIG_USB_SUPPORT=y CONFIG_SDIO_SUPPORT=n \
  CONFIG_PREALLOC_RX_SKB=y CONFIG_PREALLOC_TXQ=y modules
```

With the driver loaded, load `aicwf_rx_kunit.ko` from the build directory to
run the suite. Read its KUnit result from the kernel log or
`/sys/kernel/debug/kunit/`. The current machine's kernel has UBSAN but lacks
KUnit and KASAN, so it can compile the test object but cannot execute this
suite under those sanitizers.
