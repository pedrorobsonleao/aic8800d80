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
