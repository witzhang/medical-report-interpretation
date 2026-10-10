#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VLM 适配层回归：隐私守卫 / 离线 replay / 提案构造 / JSON 抠取。

原则：**测试不联网**。所有 VLM 相关行为用 ``provider="replay"`` 或假对象验证。

    python3 tests/test_vlm.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkup import vlm as V  # noqa: E402

TMP = tempfile.mkdtemp(prefix="_tmp_vlm_")   # 系统临时区（不落进包）


def setup_module(module):  # noqa: ANN001
    os.makedirs(TMP, exist_ok=True)


# ---------------------------------------------------------------- 隐私守卫

def test_network_requires_explicit_optin():
    """默认**不把报告发出去**：openai provider 未授权 → 拒绝并给配置方法。"""
    cfg = V.VlmConfig(provider="openai", base="http://127.0.0.1:9/v1",
                      model="m", allow_network=False)
    c = V.VlmClient(cfg)
    assert c.available
    try:
        c.read_row(b"\x89PNG")
        raise AssertionError("应当拒绝未授权的联网调用")
    except V.VlmUnavailable as e:
        msg = str(e)
        assert "未授权" in msg and "--allow-network" in msg
        assert "CHECKUP_VLM_ALLOW_NETWORK" in msg


def test_provider_none_is_unavailable_not_crash():
    c = V.VlmClient(V.VlmConfig(provider="none"))
    assert not c.available
    assert "未启用" in c.describe()


# ---------------------------------------------------------------- 离线回放

def test_replay_returns_canned_readings_offline():
    png = b"\x89PNG-row-1"
    sha = hashlib.sha1(png).hexdigest()
    path = os.path.join(TMP, "replay.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({sha: {"name": "血红蛋白浓度", "value": 158, "unit": "g/L",
                         "ref": "130-175", "flag": ""},
                   "default": {"name": "白细胞", "value": 7.5}}, f)
    c = V.VlmClient(V.VlmConfig(provider="replay", replay_path=path))
    assert c.available
    got = c.read_row(png)
    assert got["name"] == "血红蛋白浓度" and got["value"] == 158
    assert c.read_row(b"unknown-png")["name"] == "白细胞"      # default 兜底
    assert c.calls == 2 and "replay" in c.describe()


def test_replay_missing_file_is_unavailable():
    c = V.VlmClient(V.VlmConfig(provider="replay",
                                replay_path=os.path.join(TMP, "nope.json")))
    assert not c.available


# ---------------------------------------------------------------- 起草提案

def test_proposal_to_template_fills_defaults():
    """提案缺字段用默认值补齐（仍是草案，必须过验收链）。"""
    pdf = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "..", "examples", "testdata",
                       "2024-09-21_康泰综合医院.pdf")
    t = V.template_from_proposal(
        {"header_cells": ["项目", "结果", "参考值"], "column_groups": 1,
         "page_marker": "报告单", "section_x_max": 36.0,
         "required_anchors": ["康泰综合医院"], "confidence": 0.6,
         "_model": "demo"}, pdf, org_id="VLM-001")
    assert t.org_id == "VLM-001"
    assert t.lab.header_cells == ["项目", "结果", "参考值"]
    assert t.lab.column_groups == 1
    assert t.page_size[1] > 800           # 从 PDF 实测
    assert "VLM 起草提案" in (t.notes[0] if t.notes else "")


def test_proposal_without_header_cells_is_rejected():
    c = V.VlmClient(V.VlmConfig(provider="replay",
                                replay_path=os.path.join(TMP, "x.json")))
    with open(os.path.join(TMP, "x.json"), "w", encoding="utf-8") as f:
        json.dump({"default": {"notes": "看不清"}}, f)
    assert c.propose_template([b"png"], "NEW-001") is None


# ---------------------------------------------------------------- 解析

def test_strip_json_handles_code_fences_and_chatter():
    assert V._strip_json('```json\n{"a": 1}\n```')["a"] == 1
    assert V._strip_json('好的，如下：\n{"b": 2}\n以上')["b"] == 2
    assert V._strip_json("没有 JSON") is None


# ---------------------------------------------------------------- agent 两相流程

def test_agent_work_order_and_answers_roundtrip():
    """① 出工作单 → ② agent 作答 → ③ 客户端按文件名取答案（离线可测）。"""
    tasks = [{"key": "p1_r100", "png": b"\x89PNG-row", "row_text": "白细胞 7.5"},
             {"key": "p1_r200", "png": b"\x89PNG-row2", "row_text": "血红蛋白 158"}]
    work = os.path.join(TMP, "work")
    paths = V.write_work_order(work, tasks, kind="read_rows")
    assert os.path.isfile(paths["work_order"])
    assert os.path.isfile(paths["tasks"])
    assert len(os.listdir(paths["clips"])) == 2
    # 工作单必须写清"读不了图要提示用户、不许静默跳过"
    md = open(paths["work_order"], encoding="utf-8").read()
    assert "不支持图像" in md and "不要静默跳过" in md
    # agent 作答（这里由测试代写）
    answers = {"p1_r100": {"name": "白细胞", "value": 7.5, "unit": "10^9/L",
                           "ref": "4.0-10.0", "flag": "正常"}}
    with open(paths["answers"], "w", encoding="utf-8") as f:
        json.dump(answers, f, ensure_ascii=False)
    c = V.VlmClient(V.VlmConfig(provider="agent", answers_path=paths["answers"]))
    got = c.read_row(tasks[0]["png"], key="p1_r100")
    assert got["name"] == "白细胞" and got["value"] == 7.5
    # 未作答的行 → None（**不猜**）
    assert c.read_row(tasks[1]["png"], key="p1_r200") is None


def test_agent_mode_is_available_by_default():
    """agent 模式默认可用（读图能力由宿主 agent 提供），不再要求先配端点。"""
    c = V.VlmClient(V.VlmConfig(provider="agent"))
    assert c.available and "agent 本人" in c.describe()


# ------------------------------------------------------------------ 主程序

def _main() -> int:
    from checkup.console import init_console
    init_console()
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed, skipped = [], 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as e:
            failed.append(name)
            print(f"  ✗ {name}\n      {e}")
        except BaseException as e:  # noqa: BLE001
            if type(e).__name__ in ("Skipped", "DataMissing"):
                skipped += 1
                print(f"  · {name} 跳过：{e}")
                continue
            failed.append(name)
            print(f"  ! {name} 异常: {type(e).__name__}: {e}")
        else:
            print(f"  ✓ {name}")
    done = len(tests) - skipped
    print(f"\n{done - len(failed)}/{done} 通过" + (f"（跳过 {skipped}）" if skipped else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
