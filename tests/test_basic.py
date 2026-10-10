"""基础测试：默认配置、权限、未开放的群、横幅、改名、看板网址、AI 帮助。"""

import os
import sys
import json
import atexit
import shutil
import asyncio
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
data = tempfile.mkdtemp(prefix="iunoddl-test-")
atexit.register(shutil.rmtree, data, True)  # 退出时删掉临时目录
os.environ["STUB_DATA"] = data
sys.path[:0] = [str(ROOT / "tests" / "stubs"), str(ROOT / "plugin")]
import IunoDDL.IunoDDL  # noqa
from IunoDDL.IunoDDL.utils import core, store  # noqa: E402
from IunoDDL.IunoDDL import iunoddl_cmd as cmd, iunoddl_tools as tools  # noqa: E402
from IunoDDL.IunoDDL.iunoddl_cmd.service import render_list  # noqa: E402
from gsuid_core.bot import Bot  # noqa: E402
from gsuid_core.models import Event  # noqa: E402
from gsuid_core.help import draw_new_plugin_help as dh  # noqa: E402
from PIL import Image  # noqa: E402

ok = 0


def check(c, m):
    global ok
    if not c:
        raise SystemExit(f"失败：{m}")
    ok += 1


async def run(ev):
    b = Bot(ev)
    await cmd.ddl_entry(b, ev)
    return b.sent


async def main():
    cfg = store.load_config()
    raw = json.loads(store.CONFIG_PATH.read_text(encoding="utf-8"))
    check("_说明" in raw and set(raw["_说明"]) == set(store.DEFAULT_CONFIG), "默认文件带全部说明")
    check(oct(store.CONFIG_PATH.stat().st_mode)[-3:] == "600", "配置 600")
    check(cfg["bot_name"] == "尤诺" and cfg["allowed_groups"] == [] and not store.is_configured(cfg), "默认值")
    check(await run(Event(group_id="123", text="文字帮助")) == [], "未开放的群不回应")
    s = await run(Event(user_pm=1, text="文字帮助"))
    check(s[0].startswith("📒 尤诺的 DDL 小本本") and "看板：" not in s[0], "默认帮助")
    s = await run(Event(user_pm=1, text="列表"))
    check(s[0].startswith("看板的钥匙还没配好，尤诺"), "未配置提示")
    s = await run(Event(user_pm=1, text="帮助"))
    kw = dh.CALLS[-1]
    check(s == [b"IMG"] and kw["banner_sub_text"] == "哼，尤诺帮你记着呢！", "帮助图默认名字")
    check(
        kw["banner_bg"].size == (1545, 300) and kw["banner_bg"].filename.endswith("iunoddl_help/banner.jpg"), "自带横幅"
    )
    Image.new("RGB", (800, 200), (255, 0, 0)).save(os.path.join(data, "IunoDDL", "banner.jpg"))
    await run(Event(user_pm=1, text="帮助"))
    kw = dh.CALLS[-1]
    check(kw["banner_bg"].size == (800, 200), "自定义横幅")
    raw.update(
        bot_name=" 小月 ",
        board_url="https://ddl.example.com/",
        allowed_groups=[123],
        vikunja_token="x",
        project_id="3",
        view_id=9,
    )
    store.CONFIG_PATH.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    s = await run(Event(group_id="123", text="文字帮助"))
    check(
        s[0].startswith("📒 小月的 DDL 小本本") and s[0].endswith("\n\n看板：https://ddl.example.com/"), "改名后的帮助"
    )
    check(store.load_config()["project_id"] == 3 and store.is_configured(store.load_config()), "编号转整数")
    await run(Event(group_id="123", text="帮助"))
    kw = dh.CALLS[-1]
    check(
        kw["banner_sub_text"] == "哼，小月帮你记着呢！" and kw["plugin_help"]["查看"]["desc"] == "看板 ddl.example.com",
        "帮助图改名",
    )
    heads = {core.pick(core._DRAFT_HEAD) for _ in range(200)} | {core.done_head() for _ in range(200)}
    check(any("小月" in h for h in heads) and not any("{" in h or "尤诺" in h for h in heads), "俏皮话换名字")
    s = await run(Event(group_id="123", text="随便", reply="看板：https://ddl.example.com/"))
    check(s and s[0].startswith("这张卡片不能直接改哦"), "看板网址算卡片标记")
    check("看板：" not in render_list([], "") and render_list([], "https://b/").endswith("看板：https://b/"), "空看板")
    t = await tools._query_result(Event(group_id="123"), "help", "")
    check(f"@小月 问{core.by_mode('待办', '作业')}" in t, "AI 帮助")
    # 详情卡片的「说明」：插件自己生成的描述不重复显示，别的情况照常显示
    from IunoDDL.IunoDDL.iunoddl_cmd.service import render_desc, render_detail

    meta = {
        "course": "",
        "name": "报名决斗赛事",
        "platform": "",
        "detail": "",
        "sources": ["明天 报名决斗赛事"],
        "creator": "菲比（QQ 1）",
        "legacy": "",
    }
    task = {
        "id": 1,
        "index": 1,
        "title": "报名决斗赛事",
        "done": False,
        "due_date": "2026-10-13T23:59:00+08:00",
        "end_date": "0001-01-01T00:00:00Z",
        "labels": [],
        "description": render_desc(meta),
    }
    label = core.term("detail") + "："
    check(label not in render_detail(task, meta, ""), "插件记的、没写说明：不显示说明")
    check(
        label + "黑海岸会派人支援" in render_detail(task | {"description": "<p>黑海岸会派人支援</p>"}, meta, ""),
        "网页上改过描述：显示",
    )
    check(
        label + "网页上建的" in render_detail(task | {"description": "<p>网页上建的</p>"}, None, ""), "插件没记过：显示"
    )
    legacy = meta | {"creator": "", "legacy": "<p>原来的描述</p>"}
    check(label + "原来的描述" in render_detail(task, legacy, ""), "老条目：显示原来的描述")
    check(label + "记得带邀请函" in render_detail(task, meta | {"detail": "记得带邀请函"}, ""), "写了说明：照常显示")
    print(f"基础测试全部通过：{ok} 项")


asyncio.run(main())
