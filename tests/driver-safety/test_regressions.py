#!/usr/bin/env python3
"""Source-contract regressions for validated AIC8800 driver safety fixes.

Run with: python3 -m unittest discover -s tests/driver-safety -p 'test_*.py'
These checks need no root, kernel module, or adapter. They complement (not
replace) building the modules and runtime tests on hardware.
"""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
FDRV = ROOT / "drivers/aic8800/aic8800_fdrv"


def mask_comments_and_strings(source):
    pattern = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', re.S)
    return pattern.sub(lambda match: re.sub(r"[^\n]", " ", match.group()), source)


def c_function(source, name):
    masked = mask_comments_and_strings(source)
    match = re.search(r"\b" + re.escape(name) + r"\s*\([^;]*?\)\s*\{", masked, re.S)
    if not match:
        raise AssertionError(f"function {name} not found")
    start = match.end() - 1
    depth = 0
    for index in range(start, len(masked)):
        if masked[index] == "{":
            depth += 1
        elif masked[index] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start():index + 1]
    raise AssertionError(f"function {name} has unbalanced braces")


def c_block(source, marker):
    masked = mask_comments_and_strings(source)
    start = masked.find(marker)
    if start < 0:
        raise AssertionError(f"block marker not found: {marker}")
    opening = masked.find("{", start)
    if opening < 0:
        raise AssertionError(f"opening brace not found after: {marker}")
    depth = 0
    for index in range(opening, len(masked)):
        if masked[index] == "{":
            depth += 1
        elif masked[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"block is unbalanced: {marker}")


class DriverSafetyRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tx = (FDRV / "rwnx_tx.c").read_text(encoding="utf-8")
        cls.rx = (FDRV / "rwnx_rx.c").read_text(encoding="utf-8")
        cls.usb = (FDRV / "aicwf_usb.c").read_text(encoding="utf-8")
        cls.cmds = (FDRV / "rwnx_cmds.c").read_text(encoding="utf-8")
        cls.tcp_ack = (FDRV / "aicwf_tcp_ack.c").read_text(encoding="utf-8")

    def test_consumed_tx_skb_is_not_returned_as_busy(self):
        for function_name in ("intf_tx", "rwnx_start_xmit"):
            with self.subTest(function=function_name):
                function = c_function(self.tx, function_name)
                error_path = c_block(function, "if (unlikely(rwnx_prep_tx")
                self.assertIn("dev_kfree_skb_any(skb)", error_path)
                self.assertIn("return NETDEV_TX_OK", error_path)
                self.assertNotIn("return NETDEV_TX_BUSY", error_path)

    def test_amsdu_lengths_are_checked_before_subtraction_and_copy(self):
        function = c_function(self.rx, "rwnx_rxdata_process_amsdu")
        loop = c_block(function, "while (skb->len > 16)")
        minimum_check = loop.index("sublen < 6")
        allocation = loop.index("sublen - 6")
        copy = loop.index("memcpy(&sub_skb->data[12]")
        self.assertLess(minimum_check, allocation)
        self.assertLess(allocation, copy)
        self.assertIn("len_alligned > skb->len", loop)
        self.assertIn("dev_kfree_skb_any(sub_skb)", loop)

    def test_usb_probe_partial_allocations_free_each_owned_object(self):
        function = c_function(self.usb, "aicwf_usb_probe")
        cleanup = c_block(function, "if(!usb_dev->usb_tx_buf || !usb_dev->usb_rx_buf)")
        self.assertIn("vfree(usb_dev->usb_rx_buf)", cleanup)
        self.assertIn("vfree(usb_dev->usb_tx_buf)", cleanup)
        self.assertIn("kfree(usb_dev)", cleanup)
        self.assertNotIn("vfree(usb_dev)", cleanup)

    def test_command_frame_capacity_is_checked_before_buffer_write(self):
        function = c_function(self.cmds, "aicwf_set_cmd_tx")
        guard = function.index("len > CMD_BUF_MAX - 8")
        buffer_clear = function.index("memset(buffer, 0, CMD_BUF_MAX)")
        self.assertLess(guard, buffer_clear)
        self.assertIn("!msg", function[:buffer_clear])
        self.assertIn("msg->param_len != len - sizeof(*msg)", function[:buffer_clear])

    def test_tcp_ack_allocation_failure_is_checked_by_allocator_and_caller(self):
        allocator = c_function(self.tcp_ack, "intf_tcp_alloc_msg")
        failure = c_block(allocator, "if (!msg)")
        self.assertIn("return NULL", failure)

        tx = c_function(self.tx, "rwnx_start_xmit")
        allocation = tx.index("msgbuf=intf_tcp_alloc_msg(msgbuf)")
        null_check = tx.index("if (msgbuf)", allocation)
        dereference = tx.index("msgbuf->rwnx_vif", allocation)
        self.assertLess(null_check, dereference)

    def test_reorder_tid_is_bounded_and_invalid_tid_skips_reordering(self):
        flush = c_function(self.rx, "reord_flush_tid")
        flush_guard = flush.index("tid >= ARRAY_SIZE")
        flush_index = flush.index("preorder_ctrl[tid]")
        self.assertLess(flush_guard, flush_index)

        process = c_function(self.rx, "reord_process_unit")
        self.assertIn("tid >= ARRAY_SIZE", process)
        self.assertIn("return reord_single_frame_ind(rx_priv, pframe)", process)


if __name__ == "__main__":
    unittest.main(verbosity=2)
