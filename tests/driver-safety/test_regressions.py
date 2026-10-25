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


def c_call_arguments(source, name):
    masked = mask_comments_and_strings(source)
    calls = []
    for match in re.finditer(r"\b" + re.escape(name) + r"\s*\(", masked):
        opening = masked.find("(", match.start())
        depth = 0
        args = []
        arg_start = opening + 1
        for index in range(opening, len(masked)):
            if masked[index] == "(":
                depth += 1
            elif masked[index] == ")":
                depth -= 1
                if depth == 0:
                    args.append(source[arg_start:index].strip())
                    break
            elif masked[index] == "," and depth == 1:
                args.append(source[arg_start:index].strip())
                arg_start = index + 1
        calls.append(args)
    return calls


class DriverSafetyRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tx = (FDRV / "rwnx_tx.c").read_text(encoding="utf-8")
        cls.rx = (FDRV / "rwnx_rx.c").read_text(encoding="utf-8")
        cls.usb = (FDRV / "aicwf_usb.c").read_text(encoding="utf-8")
        cls.cmds = (FDRV / "rwnx_cmds.c").read_text(encoding="utf-8")
        cls.cmds_h = (FDRV / "rwnx_cmds.h").read_text(encoding="utf-8")
        cls.msg_tx = (FDRV / "rwnx_msg_tx.c").read_text(encoding="utf-8")
        cls.msg_rx = (FDRV / "rwnx_msg_rx.c").read_text(encoding="utf-8")
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

    def test_all_confirmation_destinations_pass_their_object_size(self):
        old_calls = c_call_arguments(self.msg_tx, "rwnx_send_msg")
        old_calls = [call for call in old_calls if call[-1] != "void *cfm"]
        self.assertGreater(len(old_calls), 0)
        for call in old_calls:
            with self.subTest(call=call):
                self.assertEqual(call[-1], "NULL")

        sized_calls = c_call_arguments(self.msg_tx, "rwnx_send_msg_sized")
        call_sites = [call for call in sized_calls if len(call) == 6 and call[-1] != "size_t cfm_len"]
        self.assertGreaterEqual(len(call_sites), 50)
        self.assertTrue(all("sizeof(" in call[-1] or call[-1] == "0" for call in call_sites))
        self.assertIn("size_t e2a_msg_len", self.cmds_h)
        self.assertIn("cmd->e2a_msg_len = cfm ? cfm_len : 0", self.msg_tx)

    def test_oversized_confirmation_fails_before_callback_or_copy(self):
        function = c_function(self.cmds, "cmd_mgr_msgind")
        protocol_cap = function.index("msg->param_len > RWNX_CMD_E2AMSG_LEN_MAX")
        check = function.index("msg->param_len > cmd->e2a_msg_len")
        callback = function.index("cmd_mgr_run_callback")
        copy = function.index("memcpy(cmd->e2a_msg")
        self.assertLess(protocol_cap, callback)
        self.assertLess(check, callback)
        self.assertLess(callback, copy)
        failure_path = function[check:callback]
        self.assertIn("cmd->result = -EMSGSIZE", failure_path)
        self.assertIn("RWNX_CMD_FLAG_CFM_INVALID", failure_path)
        self.assertIn("cmd->flags &= ~RWNX_CMD_FLAG_WAIT_CFM", failure_path)

        queue = c_function(self.cmds, "cmd_mgr_queue")
        result = queue.index("ret = cmd->result")
        free = queue.index("rwnx_cmd_free(cmd)", result)
        self.assertLess(result, free)

    def test_rx_station_and_vif_indices_are_checked_before_lookup(self):
        vif_lookup = c_function(self.rx, "rwnx_rx_get_vif")
        self.assertIn("vif_idx < 0 || vif_idx >= NX_VIRT_DEV_MAX", vif_lookup)
        sta_lookup = c_function(self.rx, "rwnx_rx_get_sta")
        self.assertIn("sta_idx < 0", sta_lookup)
        self.assertIn("sta_idx >= (int)ARRAY_SIZE(rwnx_hw->sta_table)", sta_lookup)

        for name in ("rwnx_rx_ps_change_ind", "rwnx_rx_traffic_req_ind"):
            function = c_function(self.msg_rx, name)
            guard = function.index("ind->sta_idx >= ARRAY_SIZE")
            lookup = function.index("&rwnx_hw->sta_table[ind->sta_idx]")
            vif_guard = function.index("sta->vif_idx >= NX_VIRT_DEV_MAX")
            vif_lookup = function.index("rwnx_hw->vif_table[sta->vif_idx]", vif_guard)
            self.assertLess(guard, lookup)
            self.assertLess(lookup, vif_guard)
            self.assertLess(vif_guard, vif_lookup)

        credit = c_function(self.tx, "rwnx_txq_credit_update")
        self.assertLess(credit.index("sta_idx < 0"), credit.index("sta_table[sta_idx]"))

    def test_firmware_message_indices_are_checked_before_array_access(self):
        msg_rx = self.msg_rx
        for name in (
            "rwnx_rx_p2p_vif_ps_change_ind",
            "rwnx_rx_rssi_status_ind",
            "rwnx_rx_pktloss_notify_ind",
        ):
            with self.subTest(function=name):
                function = c_function(msg_rx, name)
                guard = function.index("vif_idx >= NX_VIRT_DEV_MAX")
                lookup = function.index("vif_table[vif_idx]")
                self.assertLess(guard, lookup)

        survey = c_function(msg_rx, "rwnx_rx_channel_survey_ind")
        self.assertLess(survey.index("idx < 0 || idx >= ARRAY_SIZE"),
                        survey.index("survey[idx]"))

        connect = c_function(msg_rx, "rwnx_rx_sm_connect_ind")
        self.assertLess(connect.index("ind->vif_idx >= NX_VIRT_DEV_MAX"),
                        connect.index("vif_table[ind->vif_idx]"))
        self.assertLess(connect.index("ind->ap_idx >= ARRAY_SIZE"),
                        connect.index("sta_table[ind->ap_idx]"))

        disconnect = c_function(msg_rx, "rwnx_rx_sm_disconnect_ind")
        self.assertLess(disconnect.index("ind->vif_idx >= NX_VIRT_DEV_MAX"),
                        disconnect.index("vif_table[ind->vif_idx]"))
        self.assertLess(disconnect.index("if (!rwnx_vif)"),
                        disconnect.index("rwnx_vif->drv_conn_state"))

        mesh_path = c_function(msg_rx, "rwnx_rx_mesh_path_update_ind")
        self.assertLess(mesh_path.index("ind->vif_idx >= NX_VIRT_DEV_MAX"),
                        mesh_path.index("vif_table[ind->vif_idx]"))
        self.assertLess(mesh_path.index("ind->nhop_sta_idx >= ARRAY_SIZE"),
                        mesh_path.index("sta_table[ind->nhop_sta_idx]"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
