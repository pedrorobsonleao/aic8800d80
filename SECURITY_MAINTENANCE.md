# Security and performance maintenance

This workflow organizes triage and fixes for items raised in the external
`analise_seguranca_performance.md` report (generated on 2026-10-24). Treat the
report as an input for investigation, not as confirmation of bugs: validate
the code, context, and configuration in this repository before changing the
driver. The report metadata summary (9 critical/14 high) differs from the body
summary (11 critical/16 high), so do not use those totals as a progress metric.

## Validated changes

The following issues were confirmed and fixed in the current tree:

- `rwnx_start_xmit`: the `rwnx_prep_tx` error path freed the SKB and returned
  `NETDEV_TX_BUSY`; it now reports that the packet was consumed.
- RX AMSDU parser: `sublen` could be less than 6 and underflow; the parser now
  validates the minimum size, subframe length, and padding before allocating
  or copying, and frees the sub-SKB if no VIF is available.
- USB probe: partial `vmalloc` allocation failures freed the wrong object and
  could free it twice; each buffer and the owning object are now freed
  individually.
- Command transmission: verifies consistency between `len` and `param_len`,
  and checks that the frame fits in `CMD_BUF_MAX` before filling the shared
  buffer.
- TCP ACK buffer allocation: returns `NULL` immediately if `kzalloc` fails,
  avoiding `memset(NULL, ...)`; the caller also checks for failure and uses
  the normal TX path without ACK filtering.
- RX reordering: validates TID against the 8 `preorder_ctrl` entries; invalid
  TIDs skip reordering instead of indexing out of bounds.
- Firmware confirmations: callers provide the actual destination-buffer
  capacities; protocol lengths are bounded before callbacks, oversized
  responses are rejected before copying, the command completes with
  `-EMSGSIZE`, and that error is propagated to the caller.
- Firmware RX messages: validates VIF/STA indices before looking up tables in
  P2P, RSSI, packet-loss, connection/disconnection, and mesh notifications;
  also rejects negative channel indices and indices beyond the survey array.
- USB teardown: stops the TX thread/tasklet before freeing resources, cancels
  RX work and every pooled TX URB, and drains `tx_post_list`, freeing pending
  SKBs/buffers before destroying the pool. RX work is initialized before
  allocations so partial-failure cleanup can cancel it safely.

Validation recorded on 2026-10-25: `make -C drivers/aic8800` succeeded against
`/lib/modules/7.3.0-9-generic/build` (x86_64), including after the TID guard
and confirmation-size limit, and produced all three modules. The 8 checks in
`tests/driver-safety` and the 8 installer tests passed. The build emitted
fallthrough warnings in other paths and skipped BTF generation because
`vmlinux` was unavailable; there were no build errors. USB and SDIO hardware
were not exercised, so this confirms compilation, not runtime operation.

Subsequent rounds added index validation for firmware RX messages and adjusted
USB teardown. The 11 driver-safety checks passed, and `make -C
drivers/aic8800` rebuilt all three modules against the same kernel. The build
continued to emit fallthrough warnings in existing paths and skipped BTF
generation because `vmlinux` was unavailable.

## Open-item triage (2026-10-25)

An initial static review of the paths below updated the next-round priorities.
It does not replace validation of transport contracts or runtime tests.

1. **RX fragment teardown — fix applied; runtime validation pending.** In
   `rwnx_main.c`, `defrag_list` cleanup held `defrag_lock` while calling
   `timer_delete_sync()`/`del_timer_sync()` and freeing each entry.
   `defrag_timeout_cb()` in `rwnx_rx.c` also acquires `defrag_lock`. If the
   callback is running and waiting for the lock, teardown waits for the
   callback while holding that same lock. The fix detaches entries under the
   lock, waits for timers outside the lock, and coordinates ownership with the
   callback and final fragment. Concurrent disconnect/teardown still needs
   hardware testing or lockdep/KASAN coverage.

2. **RX message lengths and IDs — basic bounds fixed; functional validation
   pending.** `aicwf_txrxif.c` checks the minimum transport header, total frame
   length against remaining bytes, minimum TX confirmation size, and command
   response `param_len` consistency before dispatch. Handler lookup in
   `rwnx_msg_rx.c` also checks task and message indices against the table size.
   Fields read by each callback still need auditing and malformed input still
   needs testing on hardware/under sanitizers; treat the fix as partial with
   residual risk. In this round, panic/assert notifications were also changed
   to validate total and internal lengths before copying; unknown channel
   frequency now returns `-ENOENT` instead of triggering `BUG_ON`. The 19
   structural tests passed and `rwnx_msg_rx.o` compiled on kernel
   `7.3.0-9-generic`. Runtime/sanitizer testing with malformed messages and
   audits of other callbacks remain outstanding. Scan-result notifications
   were then changed to check the header and frame length before accessing the
   beacon/probe response; optional Wireless Extensions allocations now check
   for failure and free partial resources. The 20 structural tests passed and
   `rwnx_msg_rx.o` rebuilt successfully. The firmware log handler now ignores
   null/empty input and limits copies to the log ring buffer. After that change,
   21 structural tests passed and the object rebuilt without new warnings.
   Radar-pulse notifications now require the full structure size and validate
   chain/count before accessing detector tables; all 22 structural tests
   passed and `rwnx_msg_rx.o` rebuilt. Connection, disconnection, and external
   authentication callbacks now check fixed payload sizes; connection also
   bounds the sum of IEs to the buffer and validates context/channel
   width/frequency, while disconnection ignores a station notification on an
   AP VIF instead of calling `BUG()`. The 23 structural tests passed and
   `rwnx_msg_rx.o` rebuilt. Further size and index checks were added for
   channel-switch, TDLS, CSA, survey, RSSI/loss, MIC failure, TX-credit, and
   mesh notifications. Review of the guards also found and fixed error logs
   that could read `len` fields when only a truncated header was available.
   The 25 structural tests passed and the object rebuilt. A KUnit suite was
   added to call the production transport-length validator with malformed
   synthetic RX frames (truncated header/frame, inconsistent command response,
   oversized parameter, and undersized TX confirmation). The 26 structural
   tests passed, and the driver and KUnit objects compiled. This machine has
   no `CONFIG_KUNIT` or KASAN in its active kernel, so the suite still needs to
   run on an instrumented test kernel; it does not inject frames into firmware
   or directly cover every message callback.

3. **SDIO command buffer — fixed; hardware validation pending.**
   `aicwf_set_cmd_tx()` serializes each message in the single `bus->cmd_buf`
   before calling `aicwf_bus_txmsg()`. In SDIO,
   `aicwf_sdio_bus_txmsg()` published the same pointer as `tx_priv->cmd_buf`,
   signaled the worker, and returned before transmission. Also,
   `rwnx_send_msg_sized()` sends unconfirmed messages directly, outside the
   serialized command-manager queue. A concurrent call could overwrite bytes
   while the worker was still consuming them. The original `a2e_msg` is
   copied during serialization; the issue was ownership of the shared buffer.
   SDIO now queues each command in an exclusively owned buffer; the worker
   transmits and frees each entry, and teardown drains the queue after stopping
   the worker. Allocation occurs before publishing the entry and is undone if
   the bus stops or allocation fails. `aicwf_sdio.o`, `aicwf_txrxif.o`, and
   `rwnx_cmds.o` compiled with SDIO and USB enabled and preallocation disabled;
   a structural regression checks buffer ownership and queue flushing. SDIO
   hardware is unavailable, so concurrency and teardown still need functional
   device testing.

4. **TCP ACK timers — teardown fix applied; runtime validation pending.**
   `tcp_ack_deinit()` disables the manager and now waits for every callback
   (`del_timer_sync()`/`timer_delete_sync()`) before acquiring the seqlock and
   freeing the pending message. The wait happens outside the seqlock, which is
   also used by the callback, to avoid deadlock. Teardown calls this routine
   before `rwnx_platform_off()` and TX cache destruction, so an active callback
   can finish sending before infrastructure is dismantled. The `rwnx_main.o`
   and `aicwf_tcp_ack.o` builds and the structural regression passed;
   concurrent disconnect still needs hardware and lockdep/KASAN testing.

### RX fragment teardown update

The risk in item 1 was fixed in the working tree. Teardown detaches the list
under `defrag_lock` and cancels/waits for each timer after releasing the lock.
The callback frees an entry only if it can remove it from the list; final
fragment handling and teardown remove an entry before synchronously cancelling
its timer and freeing resources. The entry and timer are initialized before
the entry is published. Allocations inside the protected region use
`GFP_ATOMIC`.

Validation on 2026-10-25: all 13 `tests/driver-safety` checks passed;
`make -C drivers/aic8800` compiled all three modules for
`7.3.0-9-generic`/x86_64. Fallthrough warnings remain in other paths, and BTF
generation was skipped because `vmlinux` was unavailable. No disconnect/
reconnect or lockdep/KASAN test was run; runtime validation is still pending.

### RX transport bounds and message dispatch update

The parser requires a complete transport header and checks that the total
frame length, including alignment/overhead, fits the available bytes. For
command responses, it also checks the `ipc_e2a_msg` header and that the declared
payload fits the frame; for TX confirmations, it requires the 8 bytes consumed
by the handler. Callback lookup validates both indices before accessing the
table. All 15 structural checks passed and `make -C drivers/aic8800` rebuilt
the changed module successfully on kernel `7.3.0-9-generic`. No malformed
frames were injected on hardware and KASAN/UBSAN were not used; auditing
minimum lengths read by each callback remains open.

Prioritize TCP ACK timer teardown before performance optimizations. Then
continue auditing fields read by RX callbacks and the remaining memory/copy
items in the original report; current throughput measurements do not cover
malformed frames or SDIO transport.

## 1. Record and confirm each finding

For each item, open an issue or task with the report ID, file and function
(more reliable than line numbers), affected configuration (USB/SDIO, firmware,
kernel), input path, demonstrable impact, evidence, and owner. Line numbers
change and may not match the current tree.

Classify the status as **needs confirmation**, **confirmed**, **not
reproduced**, **false positive**, or **fixed**. Inspect callers, types and
bounds, error paths, and synchronization. Do not accept the report's severity
or proposed patch without review. For example, returning `NETDEV_TX_BUSY`
requires confirming SKB ownership on every path; mechanically replacing it
with `NETDEV_TX_OK` could also hide packet loss.

## 2. Prioritize the work

Address confirmed issues first when they allow out-of-bounds reads/writes,
use-after-free, memory corruption, or a hang triggered by external input. Next
address races and teardown failures, then stability and performance. Within
each class, prioritize reachable reproductions that require fewer privileges
and have greater impact. P0/P1/P2 labels in the report are provisional
priorities, not a completed risk assessment.

Group only changes that share a code path and can be reviewed together. Keep
each fix small and independent; include bounds validation, buffer ownership,
synchronization, and resource cleanup where applicable. Document any finding
that is not fixed and why.

## 3. Fix with focused review

Each change should explain the invalid condition, preserve the kernel API
contract, and cover success and failure paths. In particular:

- Validate lengths before subtracting, adding, indexing, or copying.
- Validate indices from firmware/packets before accessing arrays.
- Check execution context and allocation permissions before sleeping.
- Explicitly define SKB/URB/buffer ownership on every return path.
- Cancel and wait for callbacks before freeing USB/SDIO state.
- Reassess object lifetime and ordering before moving calls outside locks.
- Do not use `BUG()` for conditions reachable from external traffic or firmware.

Request review from someone familiar with the affected path and relevant
network, USB, or SDIO APIs. For concurrency changes, include the lock/unlock
and teardown sequence in the description.

## 4. Validate each fix

Before integration, record the commands and results. At minimum, compile
against relevant supported kernels/configurations. For protocol or memory
fixes, add a reproducible case or suitable regression test; for concurrency
and teardown, exercise load, disconnect, and reconnect. When available, use
kernel diagnostics such as KASAN, lockdep, and UBSAN in a test environment.
Do not expose a daily-use machine to reproductions that could crash the kernel.

Run the structural, hardware-free checks with
`python3 -m unittest discover -s tests/driver-safety -p 'test_*.py'`.
They protect covered source contracts but do not replace builds or runtime
tests. See the repository's [testing guide](TESTING.md) for installer,
KUnit, build, and performance commands.

Validate USB and SDIO separately when the changed code is shared or when both
implement the same contract. Record the kernel, architecture, configuration,
chipset/firmware, and result. A successful build does not confirm functional
safety.

## 5. Close and follow up

Mark an item fixed only when the change is integrated, the regression is
covered (or the limitation is documented), and validation results are
recorded. Update the report/backlog with a commit or change reference, result,
and residual risk. Reopen an item if a new reproduction contradicts the
conclusion. Revisit open findings each maintenance cycle and after major
kernel, transport, or firmware changes.

## Definition of done for each fix

- [ ] Finding confirmed in the code and correct context.
- [ ] Impact and trigger condition documented.
- [ ] Small, reviewed fix with no unintended contract change.
- [ ] Relevant build and functional validation recorded.
- [ ] Automated regression or reproducible procedure documented.
- [ ] Backlog updated with evidence and residual risk.
