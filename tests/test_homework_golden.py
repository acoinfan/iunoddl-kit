"""作业模式逐字比对：在 homework 模式下跑一批场景（帮助、草稿、列表、详情、看板描述、提示语、发给模型的提示词、
AI 工具文字），和 golden/homework-0.2.0.json 逐项比对。有意修改作业模式的措辞时，用 --update 重新生成标准答案，
并在提交说明里写清楚为什么改。"""

import os
import sys
import json
import atexit
import random
import shutil
import asyncio
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden" / "homework-0.2.0.json"
os.environ["STUB_DATA"] = tempfile.mkdtemp(prefix="iunoddl-test-")
atexit.register(shutil.rmtree, os.environ["STUB_DATA"], True)  # 退出时删掉临时目录
sys.path[:0] = [str(ROOT / "tests" / "stubs"), str(ROOT / "plugin")]
import IunoDDL.IunoDDL  # noqa
from IunoDDL.IunoDDL.utils import core, store, llm  # noqa: E402
from IunoDDL.IunoDDL import iunoddl_cmd as cmd, iunoddl_tools as tools  # noqa: E402
from IunoDDL.IunoDDL.iunoddl_cmd import service  # noqa: E402
from gsuid_core.bot import Bot  # noqa: E402
from gsuid_core.models import Event  # noqa: E402
from gsuid_core.help import draw_new_plugin_help as dh  # noqa: E402
from gsuid_core.ai_core import gs_agent  # noqa: E402

TZ = core.TZ
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=TZ)
cfg_raw = {
    "bot_name": "尤诺",
    "board_url": "https://ddl.example.com/",
    "allowed_groups": ["123"],
    "vikunja_token": "x",
    "project_id": 2,
    "view_id": 5,
}
cfg_raw["mode"] = "homework"
store.CONFIG_PATH.write_text(json.dumps(cfg_raw, ensure_ascii=False), encoding="utf-8")
cfg = store.load_config()
out = {}


def task(i, title, days, labels=(), done=False, desc=""):
    due = (NOW + timedelta(days=days)).isoformat() if days is not None else "0001-01-01T00:00:00Z"
    return {
        "id": 100 + i,
        "index": i,
        "title": title,
        "done": done,
        "due_date": due,
        "end_date": "0001-01-01T00:00:00Z",
        "labels": [{"title": label} for label in labels],
        "description": desc,
        "updated": "2026-10-10T00:00:00Z",
    }


TASKS = [
    task(1, "数值分析 · 第三次作业", 2, ["学习通"]),
    task(2, "图形学 · 实验二", 9),
    task(3, "过期的", -1, ["邮箱"]),
    task(4, "没写截止", None),
    task(5, "做完了", 1, done=True),
]


class FakeApi:
    async def list_tasks(self):
        return TASKS

    async def find_by_index(self, n):
        return next((t for t in TASKS if t["index"] == n), None)

    async def get_task(self, tid):
        return next(t for t in TASKS if t["id"] == tid)


cmd._api = lambda c: FakeApi()


def item(course, name, days, platform, detail, **kw):
    it = core.DDLItem(course, name, NOW + timedelta(days=days) if days is not None else None, platform, detail)
    for k, v in kw.items():
        setattr(it, k, v)
    return it


async def run(text, group="123", reply=None, pm=6):
    b = Bot(Event(group_id=group, text=text, reply=reply, user_pm=pm))
    await cmd.ddl_entry(b, b.ev)
    return [m if isinstance(m, str) else "<bytes>" for m in b.sent]


async def main():
    out["help_text"] = [core.help_text("https://ddl.example.com/"), core.help_text("")]
    await run("帮助")
    kw = dh.CALLS[-1]
    out["help_img"] = {k: v for k, v in kw.items() if k not in ("banner_bg", "plugin_icon")}
    out["help_img"]["plugin_help"] = {
        k: {"desc": v["desc"], "data": [{x: y for x, y in d.items() if x != "icon"} for d in v["data"]]}
        for k, v in kw["plugin_help"].items()
    }
    d_new = core.Draft(
        "new",
        "10001",
        "测试",
        0,
        items=[
            item("数值分析", "第三次作业", 3, "学习通", "1-5 题"),
            item(
                "",
                "实验报告",
                None,
                "",
                "",
                target_id=101,
                target_index=1,
                target_title="数值分析 · 第三次作业",
                target_before=item("数值分析", "第三次作业", 2, "学习通", ""),
            ),
        ],
    )
    d_new.tid = 7
    renders = {}
    for name, d in [("new", d_new)]:
        for seed in range(4):
            random.seed(seed)
            renders[f"new-{seed}"] = core.render_draft(d, NOW)
            random.seed(seed)
            renders[f"new-edited-{seed}"] = core.render_draft(d, NOW, edited=True)
    before = item("数值分析", "第三次作业", 2, "学习通", "旧要求")
    d_edit = core.Draft(
        "edit",
        "10001",
        "测试",
        0,
        items=[item("数值分析", "第三次作业", 5, "雨课堂", "新要求")],
        task_id=101,
        task_index=1,
        before=before,
    )
    d_edit.tid = 8
    d_del = core.Draft("delete", "10001", "测试", 0, task_id=101, task_index=1, before=before)
    d_del.tid = 9
    d_merge = core.Draft(
        "merge",
        "10001",
        "测试",
        0,
        items=[item("数值分析", "合并后", 4, "学习通、邮箱", "【一】x\n【二】y")],
        task_id=101,
        task_index=1,
        before=before,
        merged=[(102, 2, "图形学 · 实验二")],
    )
    d_merge.tid = 10
    renders["edit"] = core.render_draft(d_edit, NOW)
    renders["delete"] = core.render_draft(d_del, NOW)
    renders["merge"] = core.render_draft(d_merge, NOW)
    for seed in range(6):
        random.seed(seed)
        renders[f"done-{seed}"] = core.done_head()
    renders["untitled"] = core.DDLItem("", "", None, "", "").title
    out["renders"] = renders
    out["list"] = [
        service.render_list(TASKS, "https://ddl.example.com/"),
        service.render_list([], "https://ddl.example.com/"),
    ]
    meta = {
        "course": "数值分析",
        "name": "第三次作业",
        "platform": "学习通",
        "detail": "交PDF\n命名学号",
        "sources": ["原通知"],
        "creator": "测试（QQ 10001）",
        "legacy": "",
    }
    out["detail"] = service.render_detail(TASKS[0] | {"description": "<p>说明</p>"}, meta, "https://ddl.example.com/")
    out["desc"] = [service.render_desc(meta), service.render_desc(meta | {"creator": "", "legacy": "<p>老描述</p>"})]
    out["summary"] = service._change_summary(before, item("数值", "新名", 6, "邮箱", "改了"))
    msgs = {}
    for text in ("草稿", "改", "删", "合并", "#1 截止周五", "列表", "#1"):
        msgs[text] = await run(text)
    msgs["card-task-confirm"] = await run("确认", reply="📌 作业 #1：数值分析 · 第三次作业")
    msgs["card-task-empty"] = await run("", reply="📌 作业 #1：数值分析 · 第三次作业")
    msgs["card-other"] = await run("随便", reply="没完成的作业一共 3 条\n看板：https://ddl.example.com/")
    msgs["unconfigured"] = None
    gs_agent.REPLY[0] = "不是 JSON"
    msgs["parse-bad"] = await run("数值分析 第三次作业 10/18 23:59 学习通")
    gs_agent.REPLY[0] = '{"items": []}'
    msgs["parse-empty"] = await run("明天 办护照 test")
    gs_agent.REPLY[0] = (
        '{"items": [{"course": "数值分析", "name": "第三次作业", "due": "2026-10-18 23:59", '
        '"platform": "学习通", "detail": "1-5题"}]}'
    )
    random.seed(1)
    msgs["parse-ok"] = await run("数值分析 第三次作业 10/18 23:59 学习通")
    out["msgs"] = msgs
    out["prompts_parse"] = [(s, p.split("\n", 1)[1]) for s, p in gs_agent.PROMPTS]  # 去掉第一行的当前时间
    gs_agent.PROMPTS.clear()
    gs_agent.REPLY[0] = '{"changes": {"due": "2026-10-20 18:00"}}'
    await llm.edit_item(before, "截止推到下周二晚上六点", NOW)
    out["prompts_edit"] = [(s, p) for s, p in gs_agent.PROMPTS]
    ev = Event(group_id="123")
    out["tools"] = {
        "help": await tools._query_result(ev, "help", ""),
        "drafts-none": tools._draft_query(Event(group_id="123", user_id="999"), ""),
        "drafts-bad": tools._draft_query(ev, "#3"),
        "bad-action": await tools._query_result(ev, "zzz", ""),
        "prep-empty": await tools._prepare_result(ev, "", "new", "", 1),
        "prep-noref": await tools._prepare_result(ev, "改截止", "edit", "", 1),
    }
    return out


def diff(old, new, path=""):
    if type(old) is not type(new):
        return [(path, old, new)]
    if isinstance(old, dict):
        return [d for k in sorted(set(old) | set(new)) for d in diff(old.get(k), new.get(k), f"{path}.{k}")]
    if isinstance(old, list):
        if len(old) != len(new):
            return [(path, f"{len(old)} 项", f"{len(new)} 项")]
        return [d for i, (a, b) in enumerate(zip(old, new)) for d in diff(a, b, f"{path}[{i}]")]
    return [] if old == new else [(path, old, new)]


def count(x):
    if isinstance(x, dict):
        return sum(count(v) for v in x.values())
    if isinstance(x, list):
        return sum(count(v) for v in x)
    return 1


out = json.loads(json.dumps(asyncio.run(main()), ensure_ascii=False))
if "--update" in sys.argv:
    GOLDEN.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    raise SystemExit(f"已更新标准答案：{GOLDEN}")
golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
diffs = diff(golden, out)
for p, a, b in diffs[:10]:
    print(f"✗ {p}\n  标准答案：{str(a)[:200]}\n  现在输出：{str(b)[:200]}")
if diffs:
    raise SystemExit(f"作业模式的输出和标准答案有 {len(diffs)} 处不同")
print(f"作业模式逐字比对全部通过：{count(golden)} 项")
