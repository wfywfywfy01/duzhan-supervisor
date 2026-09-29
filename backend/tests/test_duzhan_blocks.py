# -*- coding: utf-8 -*-
"""督战官积木 schema：解析与校验单测（不碰网络与数据库）。"""
import json
import unittest

from app.duzhan_blocks import (
    BLOCK_TYPES,
    SOURCE_KEYS,
    as_str_list,
    block_schema,
    blocks_dict,
    dump_blocks,
    parse_blocks,
    validate_blocks,
)

GOOD = {
    "blocks": [
        {"type": "group", "channel_id": "11111111-1111-4111-8111-111111111111", "label": "示例达标群 A"},
        {"type": "times", "slots": ["10:00", "15:00", "20:00"]},
        {"type": "people", "names": ["示例成员A"]},
        {"type": "source", "key": "msg_summary"},
        {"type": "rule", "text": "本档动作：核验交付物", "when": "always"},
        {"type": "condition", "key": "workday", "value": True},
        {"type": "style", "lang": "zh", "title": "每日三追进度表"},
    ]
}


def _with(**overrides) -> list:
    blocks = [dict(item) for item in GOOD["blocks"]]
    for index, item in enumerate(blocks):
        for key, value in overrides.items():
            if item["type"] == key:
                item.update(value)
    return parse_blocks({"blocks": blocks})


class ParseTests(unittest.TestCase):
    def test_parse_accepts_json_dict_and_list(self):
        self.assertEqual(len(parse_blocks(json.dumps(GOOD))), 7)
        self.assertEqual(len(parse_blocks(GOOD)), 7)
        self.assertEqual(len(parse_blocks(GOOD["blocks"])), 7)

    def test_bad_input_is_empty_not_exception(self):
        self.assertEqual(parse_blocks("不是 JSON"), [])
        self.assertEqual(parse_blocks(""), [])
        self.assertEqual(parse_blocks(None), [])
        self.assertEqual(parse_blocks({"blocks": "x"}), [])
        self.assertEqual(parse_blocks({"blocks": ["x", 3]}), [])

    def test_dump_roundtrip_keeps_order_and_chinese(self):
        blocks = parse_blocks(GOOD)
        text = dump_blocks(blocks)
        self.assertIn("示例达标群 A", text)
        self.assertEqual([item.type for item in parse_blocks(text)], [item.type for item in blocks])
        self.assertEqual(blocks_dict(blocks)["blocks"][0]["type"], "group")

    def test_as_str_list_accepts_comma_and_list(self):
        self.assertEqual(as_str_list("10:00, 15:00"), ["10:00", "15:00"])
        self.assertEqual(as_str_list(["a", " b "]), ["a", "b"])
        self.assertEqual(as_str_list(None), [])


class ValidateTests(unittest.TestCase):
    def test_good_config_passes(self):
        self.assertEqual(validate_blocks(parse_blocks(GOOD), strategies=["promo_a"], owners=["示例成员A"]), [])

    def test_unknown_block_type(self):
        errors = validate_blocks(parse_blocks({"blocks": [{"type": "nope"}]}))
        self.assertTrue(errors)
        self.assertIn("nope", errors[0])

    def test_group_channel_must_be_uuid(self):
        errors = validate_blocks(_with(group={"channel_id": "c1"}))
        self.assertTrue(any("channel_id" in item for item in errors))

    def test_times_rejects_unsupported_hour(self):
        errors = validate_blocks(_with(times={"slots": ["12:00"]}))
        self.assertTrue(any("不支持" in item for item in errors))
        errors = validate_blocks(_with(times={"slots": ["9:00"]}))
        self.assertTrue(any("HH:MM" in item for item in errors))

    def test_people_outside_roster(self):
        errors = validate_blocks(parse_blocks(GOOD), owners=["示例成员B"])
        self.assertTrue(any("示例成员A" in item for item in errors))
        # 传 None 表示名单未知，不做人名校验
        self.assertEqual(validate_blocks(parse_blocks(GOOD), owners=None), [])

    def test_strategy_must_be_known(self):
        blocks = parse_blocks(GOOD)
        blocks.append(parse_blocks({"blocks": [{"type": "strategy", "id": "ghost"}]})[0])
        errors = validate_blocks(blocks, strategies=["promo_a"])
        self.assertTrue(any("ghost" in item for item in errors))
        self.assertEqual(validate_blocks(blocks, strategies=["promo_a", "ghost"]), [])

    def test_duplicate_slots_rejected(self):
        errors = validate_blocks(_with(times={"slots": ["10:00", "10:00"]}))
        self.assertTrue(any("重复" in item for item in errors), errors)

    def test_only_one_group_block_allowed(self):
        blocks = parse_blocks({"blocks": [
            {"type": "group", "channel_id": "11111111-1111-4111-8111-111111111111", "label": "群一"},
            {"type": "group", "channel_id": "33333333-3333-4333-8333-333333333333", "label": "群二"},
            {"type": "times", "slots": ["10:00"]},
        ]})
        errors = validate_blocks(blocks)
        self.assertTrue(any("只能有一个 group" in item for item in errors), errors)

    def test_singleton_blocks_are_capped(self):
        blocks = parse_blocks({"blocks": [
            {"type": "group", "channel_id": "11111111-1111-4111-8111-111111111111"},
            {"type": "times", "slots": ["10:00"]},
            {"type": "times", "slots": ["15:00"]},
            {"type": "style", "lang": "zh"},
            {"type": "style", "lang": "en"},
        ]})
        errors = validate_blocks(blocks)
        self.assertTrue(any("只能有一个 times" in item for item in errors), errors)
        self.assertTrue(any("只能有一个 style" in item for item in errors), errors)

    def test_empty_people_means_everyone(self):
        blocks = parse_blocks({"blocks": [
            {"type": "group", "channel_id": "11111111-1111-4111-8111-111111111111"},
            {"type": "times", "slots": ["10:00"]},
            {"type": "people", "names": []},
        ]})
        self.assertEqual(validate_blocks(blocks, owners=["示例成员A"]), [])

    def test_schema_marks_repeatable_blocks(self):
        schema = {item["type"]: item for item in block_schema()}
        for kind in ("group", "times", "people", "style"):
            self.assertFalse(schema[kind]["multiple"], kind)
        for kind in ("source", "rule", "strategy", "recipient", "condition"):
            self.assertTrue(schema[kind]["multiple"], kind)

    def test_expired_strategy_is_flagged(self):
        def with_until(until):
            return parse_blocks({"blocks": [
                {"type": "group", "channel_id": "11111111-1111-4111-8111-111111111111"},
                {"type": "times", "slots": ["10:00"]},
                {"type": "strategy", "id": "promo_a", "until": until},
            ]})

        expired = validate_blocks(with_until("2020-01-01"), strategies=["promo_a"])
        self.assertTrue(any("到期" in item for item in expired), expired)
        self.assertEqual(validate_blocks(with_until("2099-01-01"), strategies=["promo_a"]), [])
        # 用 today 注入，确认比较的是注入的那一天
        blocks = with_until("2026-01-01")
        self.assertTrue(any("到期" in item for item in validate_blocks(blocks, today="2026-06-01")))
        self.assertEqual(validate_blocks(blocks, today="2025-12-31"), [])

    def test_is_valid_day_and_today(self):
        from app.duzhan_blocks import is_valid_day, today_in_shanghai

        self.assertTrue(is_valid_day("2026-09-29"))
        self.assertFalse(is_valid_day("26-09-29"))
        self.assertFalse(is_valid_day("不是日期"))
        self.assertFalse(is_valid_day(""))
        self.assertRegex(today_in_shanghai(), r"^\d{4}-\d{2}-\d{2}$")

    def test_ai_rule_needs_prompt(self):
        blocks = parse_blocks({"blocks": [
            {"type": "group", "channel_id": "11111111-1111-4111-8111-111111111111"},
            {"type": "times", "slots": ["10:00"]},
            {"type": "rule", "mode": "ai"},
        ]})
        errors = validate_blocks(blocks)
        self.assertTrue(any("prompt" in item for item in errors))
        blocks = parse_blocks({"blocks": [
            {"type": "group", "channel_id": "11111111-1111-4111-8111-111111111111"},
            {"type": "times", "slots": ["10:00"]},
            {"type": "rule", "mode": "ai", "prompt": "按当天数据写一句催促"},
        ]})
        self.assertEqual(validate_blocks(blocks), [])

    def test_rule_text_and_when(self):
        errors = validate_blocks(_with(rule={"text": "  "}))
        self.assertTrue(any("规则文案" in item for item in errors))
        errors = validate_blocks(_with(rule={"when": "fullmoon"}))
        self.assertTrue(any("when" in item for item in errors))

    def test_condition_value_must_be_bool(self):
        errors = validate_blocks(_with(condition={"value": "yes"}))
        self.assertTrue(any("true/false" in item for item in errors))

    def test_recipient_needs_uuid(self):
        blocks = parse_blocks({"blocks": [
            {"type": "group", "channel_id": "11111111-1111-4111-8111-111111111111"},
            {"type": "times", "slots": ["10:00"]},
            {"type": "recipient", "kind": "sms", "id": "abc"},
        ]})
        errors = validate_blocks(blocks)
        self.assertTrue(any("kind" in item for item in errors))
        self.assertTrue(any("UUID" in item for item in errors))

    def test_style_lang_and_title(self):
        self.assertTrue(any("lang" in item for item in validate_blocks(_with(style={"lang": "fr"}))))
        self.assertTrue(any("title" in item for item in validate_blocks(_with(style={"title": "  "}))))

    def test_missing_group_or_times(self):
        blocks = parse_blocks({"blocks": [{"type": "source", "key": "msg_summary"}]})
        errors = validate_blocks(blocks)
        self.assertTrue(any("group" in item for item in errors))
        self.assertTrue(any("times" in item for item in errors))
        self.assertEqual(validate_blocks([]), ["至少需要一个积木块（至少要有 group + times）"])


class SchemaTests(unittest.TestCase):
    def test_schema_covers_every_block_type(self):
        schema = block_schema()
        self.assertEqual([item["type"] for item in schema], list(BLOCK_TYPES))
        for item in schema:
            self.assertTrue(item["label"] and item["fields"])

    def test_source_options_match_module(self):
        schema = {item["type"]: item for item in block_schema()}
        options = schema["source"]["fields"][0]["options"]
        self.assertEqual(options, list(SOURCE_KEYS.keys()))


if __name__ == "__main__":
    unittest.main()
