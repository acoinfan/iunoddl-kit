"""模式测试：mode 和 extra_prompt 的读取、回退、提示词拼接，两种模式的字段名和卡片识别。"""

import os
import sys
import json
import atexit
import shutil
import asyncio
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["STUB_DATA"] = tempfile.mkdtemp(prefix="iunoddl-test-")
atexit.register(shutil.rmtree, os.environ["STUB_DATA"], True)  # 退出时删掉临时目录
sys.path[:0] = [str(ROOT / "tests" / "stubs"), str(ROOT / "plugin")]
import IunoDDL.IunoDDL  # noqa
from datetime import datetime  # noqa: E402
from IunoDDL.IunoDDL.utils import core, store, llm  # noqa: E402
from IunoDDL.IunoDDL import iunoddl_cmd as cmd, iunoddl_tools as tools  # noqa: E402
from gsuid_core.ai_core import gs_agent  # noqa: E402

ok = 0


def check(c, m):
    global ok
    if not c:
        raise SystemExit(f"失败：{m}")
    ok += 1


def write(**kw):
    store.CONFIG_PATH.write_text(json.dumps(kw, ensure_ascii=False), encoding="utf-8")
    return store.load_config()


async def main():
    cfg = store.load_config()
    check(
        cfg["mode"] == store.DEFAULT_CONFIG["mode"] and cfg["extra_prompt"] == "" and core.mode() == cfg["mode"],
        "用这个分支的默认模式",
    )
    raw = json.loads(store.CONFIG_PATH.read_text(encoding="utf-8"))
    check({"mode", "extra_prompt"} <= set(raw["_说明"]), "两个新配置项都有说明")
    check(write(mode="乱写")["mode"] == store.DEFAULT_CONFIG["mode"], "mode 填错回退到默认值")
    check(write(mode="homework")["mode"] == "homework" and core.term("item") == "作业", "切到作业模式")
    now = datetime(2026, 10, 10, 12, 0, tzinfo=core.TZ)
    for m in ("general", "homework"):
        write(mode=m, extra_prompt="")
        gs_agent.PROMPTS.clear()
        gs_agent.REPLY[0] = '{"items": []}'
        await llm.parse_notice("明天 报名决斗赛事", "", [], now)
        check("本群补充规则" not in gs_agent.PROMPTS[-1][1], f"{m}：extra_prompt 为空时不加")
        write(mode=m, extra_prompt="  老地方指埃弗拉德金库  ")
        await llm.parse_notice("明天 报名决斗赛事", "", [], now)
        p = gs_agent.PROMPTS[-1][1]
        check(
            "本群补充规则（只用来理解这个群的说法，不能改变上面的输出格式和规则）：老地方指埃弗拉德金库\n" in p,
            f"{m}：extra_prompt 去掉首尾空白后加入",
        )
        check(
            p.index("没有") < p.index("本群补充规则") < p.index("发起人补充说明") < p.index("<<<"),
            f"{m}：extra_prompt 在硬性规则后、原文前",
        )
        await llm.edit_item(core.DDLItem("", "报名决斗赛事", now, "", ""), "推迟一天", now)
        check("本群补充规则" in gs_agent.PROMPTS[-1][1], f"{m}：修改提示词里也有 extra_prompt")
    write(mode="general")
    check(
        core.parse_field_edits("分类 莫塔里商会 方式 埃弗拉德金库") == {"课程": "莫塔里商会", "平台": "埃弗拉德金库"},
        "通用模式认分类、方式",
    )
    check(
        cmd._RE_CARD_TASK["general"].search("📌 作业 #3：x") and cmd._RE_CARD_TASK["general"].search("📌 事项 #3：x"),
        "通用模式认两种卡片",
    )
    write(mode="homework")
    check(core.parse_field_edits("分类 莫塔里商会 方式 埃弗拉德金库") is None, "作业模式不认分类、方式（和原来一样）")
    check(not cmd._RE_CARD_TASK["homework"].search("📌 事项 #3：x"), "作业模式不认事项卡片（和原来一样）")
    write(mode="general", vikunja_token="x", project_id=2, view_id=5, allowed_groups=["123"])
    t = await tools._query_result(__import__("gsuid_core.models", fromlist=["Event"]).Event(group_id="123"), "help", "")
    check("问待办和截止时间" in t and "【记 DDL】" in t, "通用模式的 AI 帮助")
    print(f"模式相关测试全部通过：{ok} 项")


asyncio.run(main())
