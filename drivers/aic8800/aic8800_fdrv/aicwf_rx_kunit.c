// SPDX-License-Identifier: GPL-2.0
#include <kunit/test.h>
#include <linux/module.h>
#include <linux/align.h>
#include "aicwf_txrxif.h"
#include "lmac_msg.h"

static void aicwf_rx_set_u16(u8 *dst, u16 value)
{
	dst[0] = value & 0xff;
	dst[1] = value >> 8;
}

static u8 *aicwf_rx_test_frame(struct kunit *test, size_t size)
{
	u8 *frame = kunit_kzalloc(test, size, GFP_KERNEL);

	return frame;
}

static void aicwf_rx_rejects_short_headers(struct kunit *test)
{
	u8 frame[4] = { 0 };

	KUNIT_EXPECT_FALSE(test, aicwf_rx_pkt_len_valid(NULL, sizeof(frame)));
	KUNIT_EXPECT_FALSE(test, aicwf_rx_pkt_len_valid(frame, 0));
	KUNIT_EXPECT_FALSE(test, aicwf_rx_pkt_len_valid(frame, 3));
	KUNIT_EXPECT_FALSE(test, aicwf_rx_pkt_len_valid(frame, sizeof(frame)));
}

static void aicwf_rx_checks_data_frame_extent(struct kunit *test)
{
	u8 *frame = aicwf_rx_test_frame(test, RX_HWHRD_LEN + 64);
	const u16 payload_len = 64;

	KUNIT_ASSERT_NOT_NULL(test, frame);
	aicwf_rx_set_u16(frame, payload_len);
	frame[2] = USB_TYPE_DATA;

	KUNIT_EXPECT_FALSE(test, aicwf_rx_pkt_len_valid(
		frame, payload_len + RX_HWHRD_LEN - 1));
	KUNIT_EXPECT_TRUE(test, aicwf_rx_pkt_len_valid(
		frame, payload_len + RX_HWHRD_LEN));
}

static void aicwf_rx_checks_command_response_lengths(struct kunit *test)
{
	const size_t ipc_header = offsetof(struct ipc_e2a_msg, param);
	const size_t param_len_offset = offsetof(struct ipc_e2a_msg, param_len);
	const u16 payload_len = 4;
	const u16 pkt_len = ipc_header + payload_len;
	const size_t frame_len = ALIGN((size_t)pkt_len + 4, RX_ALIGNMENT);
	const u16 oversized_param_len = sizeof(((struct ipc_e2a_msg *)0)->param) + 1;
	const u16 oversized_pkt_len = ipc_header + oversized_param_len;
	const size_t oversized_frame_len =
		ALIGN((size_t)oversized_pkt_len + 4, RX_ALIGNMENT);
	u8 *frame = aicwf_rx_test_frame(test, oversized_frame_len);

	KUNIT_ASSERT_NOT_NULL(test, frame);
	aicwf_rx_set_u16(frame, pkt_len);
	frame[2] = USB_TYPE_CFG_CMD_RSP;
	aicwf_rx_set_u16(frame + 4 + param_len_offset, payload_len);

	KUNIT_EXPECT_TRUE(test, aicwf_rx_pkt_len_valid(frame, frame_len));
	KUNIT_EXPECT_FALSE(test, aicwf_rx_pkt_len_valid(frame, frame_len - 1));

	/* The embedded parameter length cannot exceed the bytes in the frame. */
	aicwf_rx_set_u16(frame + 4 + param_len_offset, payload_len + 1);
	KUNIT_EXPECT_FALSE(test, aicwf_rx_pkt_len_valid(frame, frame_len));

	/* Reject a declared parameter larger than the IPC destination buffer. */
	aicwf_rx_set_u16(frame, oversized_pkt_len);
	aicwf_rx_set_u16(frame + 4 + param_len_offset, oversized_param_len);
	KUNIT_EXPECT_FALSE(test, aicwf_rx_pkt_len_valid(frame, oversized_frame_len));
}

static void aicwf_rx_checks_command_header_and_confirmation(struct kunit *test)
{
	const size_t ipc_header = offsetof(struct ipc_e2a_msg, param);
	const u16 short_cmd_len = ipc_header - 1;
	size_t frame_len = ALIGN((size_t)short_cmd_len + 4, RX_ALIGNMENT);
	u8 *frame = aicwf_rx_test_frame(test, sizeof(struct ipc_e2a_msg) + 16);

	KUNIT_ASSERT_NOT_NULL(test, frame);
	aicwf_rx_set_u16(frame, short_cmd_len);
	frame[2] = USB_TYPE_CFG_CMD_RSP;
	KUNIT_EXPECT_FALSE(test, aicwf_rx_pkt_len_valid(frame, frame_len));

	aicwf_rx_set_u16(frame, ipc_header + sizeof(u32));
	frame_len = ALIGN((size_t)ipc_header + sizeof(u32) + 4, RX_ALIGNMENT);
	KUNIT_EXPECT_FALSE(test, aicwf_rx_pkt_len_valid(
		frame, 4 + ipc_header - 1));

	aicwf_rx_set_u16(frame, sizeof(u32));
	frame[2] = USB_TYPE_CFG_DATA_CFM;
	frame_len = ALIGN((size_t)sizeof(u32) + 4, RX_ALIGNMENT);
	KUNIT_EXPECT_FALSE(test, aicwf_rx_pkt_len_valid(frame, frame_len));

	aicwf_rx_set_u16(frame, sizeof(u32) * 2);
	frame_len = ALIGN((size_t)sizeof(u32) * 2 + 4, RX_ALIGNMENT);
	KUNIT_EXPECT_TRUE(test, aicwf_rx_pkt_len_valid(frame, frame_len));
}

static void aicwf_rx_rejects_zero_and_overflowing_lengths(struct kunit *test)
{
	u8 frame[RX_HWHRD_LEN + 4] = { 0 };

	frame[2] = USB_TYPE_DATA;
	KUNIT_EXPECT_FALSE(test, aicwf_rx_pkt_len_valid(frame, sizeof(frame)));

	aicwf_rx_set_u16(frame, 0xffff);
	KUNIT_EXPECT_FALSE(test, aicwf_rx_pkt_len_valid(frame, sizeof(frame)));
}

static struct kunit_case aicwf_rx_parser_cases[] = {
	KUNIT_CASE(aicwf_rx_rejects_short_headers),
	KUNIT_CASE(aicwf_rx_checks_data_frame_extent),
	KUNIT_CASE(aicwf_rx_checks_command_response_lengths),
	KUNIT_CASE(aicwf_rx_checks_command_header_and_confirmation),
	KUNIT_CASE(aicwf_rx_rejects_zero_and_overflowing_lengths),
	{}
};

static struct kunit_suite aicwf_rx_parser_suite = {
	.name = "aicwf-rx-parser",
	.test_cases = aicwf_rx_parser_cases,
};

kunit_test_suite(aicwf_rx_parser_suite);

MODULE_LICENSE("GPL");
