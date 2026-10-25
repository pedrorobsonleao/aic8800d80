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
        cls.txrxif = (FDRV / "aicwf_txrxif.c").read_text(encoding="utf-8")
        cls.sdio = (FDRV / "aicwf_sdio.c").read_text(encoding="utf-8")
        cls.main = (FDRV / "rwnx_main.c").read_text(encoding="utf-8")
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

    def test_tcp_ack_timers_are_drained_before_locks_and_platform_teardown(self):
        deinit = c_function(self.tcp_ack, "tcp_ack_deinit")
        timer_wait = deinit.index("timer_delete_sync")
        lock = deinit.index("write_seqlock_bh", timer_wait)
        self.assertLess(timer_wait, lock)
        self.assertIn("del_timer_sync", deinit)

        teardown = c_function(self.main, "rwnx_cfg80211_deinit")
        ack_deinit = teardown.index("tcp_ack_deinit(rwnx_hw)")
        platform_off = teardown.index("rwnx_platform_off(rwnx_hw")
        cache_destroy = teardown.index("kmem_cache_destroy", platform_off)
        self.assertLess(ack_deinit, platform_off)
        self.assertLess(ack_deinit, cache_destroy)

    def test_firmware_panic_and_assert_payloads_are_bounded(self):
        panic = c_function(self.msg_rx, "rwnx_fw_panic_ind")
        self.assertLess(panic.index("msg->param_len < sizeof(*ind)"),
                        panic.index("memcpy(version"))

        assertion = c_function(self.msg_rx, "rwnx_fw_assert_ind")
        guard = assertion.index("ind->len >= sizeof(buffer)")
        copy = assertion.index("memcpy(buffer, ind->info, ind->len)")
        self.assertLess(assertion.index("msg->param_len < offsetof"), guard)
        self.assertLess(guard, copy)
        self.assertIn("ind->len > msg->param_len - offsetof", assertion)

    def test_unknown_firmware_survey_frequency_does_not_trigger_bug(self):
        mapper = c_function(self.msg_rx, "rwnx_freq_to_idx")
        self.assertIn("return -ENOENT", mapper)
        self.assertNotIn("BUG_ON", mapper)
        survey = c_function(self.msg_rx, "rwnx_rx_channel_survey_ind")
        self.assertLess(survey.index("idx < 0"), survey.index("&rwnx_hw->survey[idx]"))

    def test_scan_result_frame_and_wireless_extensions_allocations_are_bounded(self):
        scan = c_function(self.msg_rx, "rwnx_rx_scanu_result_ind")
        guard = scan.index("ind->length > msg->param_len")
        channel = scan.index("ieee80211_get_channel")
        self.assertLess(scan.index("msg->param_len < offsetof"), guard)
        self.assertLess(guard, channel)
        self.assertIn("ind->length < offsetof(struct ieee80211_mgmt", scan)
        self.assertIn("if (!scan_re_wext->ind || !scan_re_wext->payload)", scan)
        self.assertIn("vfree(scan_re_wext->payload)", scan)

    def test_firmware_log_rejects_empty_input_and_caps_ring_copy(self):
        function = c_function(self.msg_rx, "rwnx_rx_handle_print")
        guard = function.index("if (!rwnx_hw || !msg || !len)")
        clamp = function.index("len = min_t(u32, len, FW_LOG_SIZE)")
        terminator = function.index("msg[len-1] = '\\0'")
        self.assertLess(guard, clamp)
        self.assertLess(clamp, terminator)

    def test_radar_indication_checks_payload_and_chain_before_indexing(self):
        radar = c_function(self.msg_rx, "rwnx_radar_detect_ind")
        length_guard = radar.index("msg->param_len < sizeof(*pulses)")
        chain_guard = radar.index("pulses->idx >= RWNX_RADAR_LAST")
        detector = radar.index("rwnx_radar_detection_is_enable")
        pulse_array = radar.index("radar.pulses[pulses->idx]")
        self.assertLess(length_guard, chain_guard)
        self.assertLess(chain_guard, detector)
        self.assertLess(detector, pulse_array)
        self.assertIn("pulses->cnt > RADAR_PULSE_MAX", radar)

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

    def test_usb_teardown_stops_tx_before_killing_and_freeing_urbs(self):
        teardown = c_function(self.usb, "aicwf_usb_cancel_all_urbs_")
        self.assertLess(teardown.index("cancel_work_sync(&usb_dev->rx_urb_work)"),
                        teardown.index("usb_kill_urb"))
        self.assertIn("for (i = 0; i < AICWF_USB_TX_URBS; i++)", teardown)
        self.assertIn("usb_dev->usb_tx_buf[i].urb", teardown)

        deinit = c_function(self.usb, "aicwf_usb_deinit")
        drain = deinit.index("&usbdev->tx_post_list")
        free_tx_pool = deinit.index("&usbdev->tx_free_list")
        self.assertLess(drain, free_tx_pool)

        bus_deinit = c_function((FDRV / "aicwf_txrxif.c").read_text(),
                                 "aicwf_bus_deinit")
        stop_thread = bus_deinit.index("kthread_stop(bus_if->bustx_thread)")
        kill_tasklet = bus_deinit.index("tasklet_kill(&usb->xmit_tasklet)")
        cancel = bus_deinit.index("aicwf_usb_cancel_all_urbs(usb)")
        self.assertLess(stop_thread, cancel)
        self.assertLess(kill_tasklet, cancel)

    def test_fragment_timeout_and_teardown_do_not_wait_under_defrag_lock(self):
        timeout = c_block(self.rx, "void defrag_timeout_cb(ulong data)")
        self.assertIn("if (!list_empty(&defrag_ctrl->list))", timeout)
        self.assertLess(timeout.index("spin_unlock_bh"),
                        timeout.index("dev_kfree_skb"))

        deinit = c_function(self.main, "rwnx_cfg80211_deinit")
        detach = deinit.index("list_splice_init(&rwnx_hw->defrag_list")
        unlock = deinit.index("spin_unlock_bh(&rwnx_hw->defrag_lock)", detach)
        wait_timer = min(pos for pos in (
            deinit.find("timer_delete_sync(&defrag_ctrl->defrag_timer)", unlock),
            deinit.find("del_timer_sync(&defrag_ctrl->defrag_timer)", unlock),
        ) if pos >= 0)
        self.assertLess(detach, unlock)
        self.assertLess(unlock, wait_timer)
        self.assertLess(wait_timer, deinit.index("kfree(defrag_ctrl)", wait_timer))

    def test_fragment_entry_timer_is_initialized_before_publication(self):
        rxdata = c_function(self.rx, "rwnx_rxdataind_aicwf")
        publish = rxdata.index("list_add_tail(&defrag_info->list")
        setup_positions = [
            pos for pos in (rxdata.find("timer_setup(&defrag_info->defrag_timer"),
                            rxdata.find("init_timer(&defrag_info->defrag_timer"))
            if pos >= 0
        ]
        self.assertTrue(setup_positions)
        self.assertLess(max(setup_positions), publish)
        self.assertLess(publish,
                        rxdata.index("mod_timer(&defrag_info->defrag_timer"))
        self.assertIn("kzalloc(sizeof(struct defrag_ctrl_info), GFP_ATOMIC)", rxdata)

    def test_rx_transport_length_validation_precedes_frame_access(self):
        validate = c_function(self.txrxif, "aicwf_rx_pkt_len_valid")
        self.assertIn("available < 4", validate)
        self.assertIn("frame_len <= available", validate)
        self.assertIn("pkt_len - offsetof(struct ipc_e2a_msg, param) < param_len",
                      validate)
        self.assertIn("type == USB_TYPE_CFG_DATA_CFM && pkt_len < sizeof(u32) * 2",
                      validate)

        aggregate = c_function(self.txrxif, "aicwf_another_ptk")
        self.assertIn("aicwf_rx_pkt_len_valid(skb->data, skb->len)", aggregate)
        frames = c_function(self.txrxif, "aicwf_process_rxframes")
        self.assertGreaterEqual(frames.count("if (!aicwf_rx_pkt_len_valid"), 2)

    def test_firmware_message_id_is_bounded_before_handler_lookup(self):
        lookup = c_function(self.msg_rx, "rwnx_msg_handler_get")
        bounded = re.findall(
            r"if \(index < ARRAY_SIZE\((\w+)\)\)\s*return \1\[index\];",
            lookup,
        )
        self.assertEqual(len(bounded), 8)
        handler = c_function(self.msg_rx, "rwnx_rx_handle_msg")
        self.assertIn("handler = rwnx_msg_handler_get(msg->id)", handler)
        self.assertNotIn("msg_hdlrs[MSG_T(msg->id)]", handler)

    def test_sdio_commands_own_buffers_until_worker_sends_them(self):
        set_cmd = c_function(self.cmds, "aicwf_set_cmd_tx")
        self.assertIn("kzalloc(CMD_BUF_MAX + SDIOWIFI_FUNC_BLOCKSIZE, GFP_ATOMIC)",
                      set_cmd)
        self.assertLess(set_cmd.index("kzalloc(CMD_BUF_MAX"),
                        set_cmd.index("aicwf_bus_txmsg(bus, buffer"))
        self.assertIn("if (ret < 0)", set_cmd)
        self.assertIn("kfree(buffer)", set_cmd)

        enqueue = c_function(self.sdio, "aicwf_sdio_bus_txmsg")
        self.assertLess(enqueue.index("list_add_tail(&cmd->list"),
                        enqueue.index("complete(&bus_if->bustx_trgg)"))

        worker = c_function(self.sdio, "aicwf_sdio_tx_process")
        pop = worker.index("list_del(&cmd->list)")
        send = worker.index("aicwf_sdio_tx_msg(sdiodev)", pop)
        free = worker.index("kfree(cmd->data)", send)
        self.assertLess(pop, send)
        self.assertLess(send, free)

        teardown = c_function(self.sdio, "aicwf_sdio_bus_stop")
        self.assertLess(teardown.index("down(&sdiodev->tx_priv->txctl_sema)"),
                        teardown.index("aicwf_sdio_cmd_queue_flush(sdiodev)"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
