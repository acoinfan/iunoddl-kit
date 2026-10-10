"""生成使用示例：群聊截图（docs/images/示例-*.png）和合订本 docs/使用示例.pdf。

尤诺的每一条回复都由插件真实的代码生成：用 tests/stubs 里的替身导入插件，接一个内存里的假看板，
时间固定在 2026-09-07（周一）20:00，随机俏皮话固定种子，所以每次生成的图都一样。
只有「模型从通知里解析出什么」是预设的（真实效果取决于你配置的模型），图上会注明。

用法（需要 Pillow，以及 Noto Sans CJK 和 Noto Color Emoji 字体）：
    python3 docs/examples/make_examples.py
"""

import io
import os
import sys
import json
import atexit
import random
import shutil
import asyncio
import tempfile
import subprocess
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[2]
OUT_IMAGES = ROOT / "docs" / "images"
OUT_PDF = ROOT / "docs" / "使用示例.pdf"
os.environ["STUB_DATA"] = tempfile.mkdtemp(prefix="iunoddl-examples-")
atexit.register(shutil.rmtree, os.environ["STUB_DATA"], True)  # 退出时删掉临时目录
sys.path[:0] = [str(ROOT / "tests" / "stubs"), str(ROOT / "plugin")]

import IunoDDL.IunoDDL  # noqa: E402,F401
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from IunoDDL.IunoDDL import iunoddl_cmd as cmd, iunoddl_tools as tools  # noqa: E402
from IunoDDL.IunoDDL.utils import core, store  # noqa: E402
from IunoDDL.IunoDDL.iunoddl_cmd import service  # noqa: E402
from IunoDDL.IunoDDL.utils.vikunja import VikunjaError  # noqa: E402

from gsuid_core.bot import Bot  # noqa: E402
from gsuid_core.models import Event  # noqa: E402
from gsuid_core.ai_core import gs_agent  # noqa: E402

TZ = core.TZ
NOW = datetime(2026, 9, 7, 20, 0, tzinfo=TZ)  # 周一晚上 8 点
GROUP = "123456"
BOARD = "https://ddl.example.com/"


# ---------------------------------------------------------------- 固定时间


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return NOW if tz is not None else NOW.replace(tzinfo=None)


for module in (cmd, service, core, tools):
    module.datetime = FrozenDatetime  # type: ignore[attr-defined]


# ---------------------------------------------------------------- 内存里的假看板


class FakeBoard:
    """照真实 Vikunja 接口的签名实现插件用到的 9 个方法。"""

    def __init__(self) -> None:
        self.tasks: dict[int, dict] = {}
        self.next_id, self.next_index, self.stamp = 101, 1, 0

    def _touch(self, task: dict) -> None:
        self.stamp += 1
        task["updated"] = f"2026-10-12T20:00:{self.stamp:02d}+08:00"

    async def list_tasks(self):
        return [json.loads(json.dumps(t)) for t in self.tasks.values()]

    async def find_by_index(self, index):
        return next((json.loads(json.dumps(t)) for t in self.tasks.values() if t["index"] == index), None)

    async def get_task(self, task_id):
        if task_id not in self.tasks:
            raise VikunjaError(f"GET /tasks/{task_id} -> 404", 404)
        return json.loads(json.dumps(self.tasks[task_id]))

    async def create_task(self, title, description, due):
        task = {
            "id": self.next_id,
            "index": self.next_index,
            "title": title,
            "description": description,
            "done": False,
            "due_date": due.isoformat() if due else "0001-01-01T00:00:00Z",
            "end_date": "0001-01-01T00:00:00Z",
            "labels": [],
        }
        self._touch(task)
        self.tasks[self.next_id] = task
        self.next_id += 1
        self.next_index += 1
        return json.loads(json.dumps(task))

    async def update_task(self, task_id, title, description, due):
        task = self.tasks[task_id]
        task.update(title=title, description=description, due_date=due.isoformat() if due else "0001-01-01T00:00:00Z")
        self._touch(task)
        return json.loads(json.dumps(task))

    async def delete_task(self, task_id):
        self.tasks.pop(task_id, None)

    async def ensure_label(self, title):
        return abs(hash(title)) % 1000

    async def set_labels(self, task_id, titles):
        self.tasks[task_id]["labels"] = [{"title": t} for t in titles if t]

    async def add_comment(self, task_id, html):
        pass


BOARD_STATE = FakeBoard()
cmd._api = lambda cfg: BOARD_STATE  # type: ignore[assignment]


def configure(mode: str) -> None:
    cfg = {
        "bot_name": "尤诺",
        "mode": mode,
        "board_url": BOARD,
        "allowed_groups": [GROUP],
        "vikunja_token": "示例",
        "project_id": 2,
        "view_id": 5,
    }
    store.CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    store.load_config()


# ---------------------------------------------------------------- 跑一段对话

PEOPLE = {
    "菲比": ("20001", (232, 160, 87)),
    "卡提希娅": ("20002", (93, 150, 214)),
    "守岸人": ("20003", (120, 170, 160)),
    "达妮娅": ("20004", (204, 108, 120)),
    "小爱": ("20005", (238, 130, 170)),
    "珂莱塔": ("20006", (150, 120, 200)),
    "奥古斯塔": ("20007", (214, 170, 70)),
    "露帕": ("20008", (220, 90, 70)),
    "西格莉卡": ("20009", (230, 150, 60)),
    "琳奈": ("20010", (160, 160, 150)),
    "今汐": ("20011", (120, 180, 200)),
}


def model_says(items: list[dict]) -> None:
    """预设模型这一次的解析结果。"""
    gs_agent.REPLY[0] = json.dumps({"items": items}, ensure_ascii=False)


class Chat:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def say(self, who: str, text: str, quote: int | None = None, group: str = GROUP) -> int:
        """发一条消息；quote 是要引用的那条消息的序号。返回这条消息的序号。"""
        uid, _ = PEOPLE[who]
        quoted = self.messages[quote] if quote is not None else None
        body = text[3:].strip() if text.lower().startswith("ddl") else text
        ev = Event(
            group_id=group, user_id=uid, sender={"nickname": who}, text=body, reply=quoted["text"] if quoted else None
        )
        bot = Bot(ev)
        random.seed(len(self.messages))
        if text.startswith("@尤诺"):
            bot.sent.append(await tools._query_result(ev, "list", ""))
        else:
            await cmd.ddl_entry(bot, ev)
        self.messages.append({"who": who, "text": text, "quote": quoted})
        mine = len(self.messages) - 1
        for reply in bot.sent:
            assert isinstance(reply, str), "示例里不放帮助图"
            self.messages.append({"who": "尤诺", "text": reply, "quote": None})
        return mine

    def last_bot(self) -> int:
        return max(i for i, m in enumerate(self.messages) if m["who"] == "尤诺")

    def quote_note(self, who: str, text: str) -> int:
        """群里别人发的一条普通消息（不是 ddl 指令），用来被引用。"""
        self.messages.append({"who": who, "text": text, "quote": None})
        return len(self.messages) - 1


def item(name: str, due: str, course: str = "", platform: str = "", detail: str = "") -> dict:
    return {"course": course, "name": name, "due": due, "platform": platform, "detail": detail}


def scene(title: str, chat: Chat, *notes: str) -> dict:
    return {"title": title, "chat": chat, "notes": list(notes)}


# 第 9 幕的作业：题号真的很多
HOMEWORK = (
    "3.1 3.3 3.7 3.12 3.18 3.21 3.26 3.33 3.38 3.42 3.47 3.51 3.58 3.64 3.69 3.73 3.80 3.86 3.91 3.97 3.105 3.120"
)


def index_of(title_part: str) -> int:
    """看板上标题里含这几个字的那条现在是几号。"""
    return next(t["index"] for t in BOARD_STATE.tasks.values() if title_part in t["title"])


async def build_scenes() -> list[dict]:
    """九个场景，设定按空白定的来；具体事件都是为了演示编的。"""
    scenes = []
    configure("general")

    # 示例开始前看板上就记着的三条（第 6、7 幕要用），走真实流程记下，但不画出来
    before = Chat()
    model_says([item("漂泊者回来过追月节", "2026-09-15 23:59", "今州")])
    await before.say("今汐", "ddl 9月15日 追月节 漂泊者回来")
    await before.say("今汐", "ddl确认", quote=before.last_bot())
    model_says([item("查看海维夏探测器的状态", "2026-09-10 23:59", "莫宁教授")])
    await before.say("琳奈", "ddl 周四 莫宁教授让我去看海维夏探测器的状态")
    await before.say("琳奈", "ddl确认", quote=before.last_bot())
    model_says([item("重新做入学考核", "2026-09-11 23:59", "洛瑟菈校长")])
    await before.say("琳奈", "ddl 周五 洛瑟菈校长要给我重新做入学考核")
    await before.say("琳奈", "ddl确认", quote=before.last_bot())
    # 把这之前当成机器人重启前：草稿清空、编号从 T1 重新开始（真实插件重启后也是这样），看板上的条目照样保留
    cmd.DRAFTS.clear()
    cmd._next_tid = 1

    chat = Chat()
    model_says([item("报名决斗赛事", "2026-09-08 23:59")])
    await chat.say("露帕", "ddl 明天 报名决斗赛事")
    await chat.say("露帕", "ddl确认", quote=chat.last_bot())
    scenes.append(
        scene(
            "一句话记一件事",
            chat,
            "发「ddl」加一句带日期的话，尤诺先整理成草稿，这时还没写进看板。",
            "长按草稿卡片 →「回复」，发 ddl确认 才真正写进去。引用了卡片，就不用写草稿编号。",
        )
    )

    chat = Chat()
    notice = chat.quote_note(
        "奥古斯塔", "【七丘】决战利维亚坦定于9月12日18:00开始，各部于[巡游天国]的入口——三王峰集合。"
    )
    model_says([item("决战利维亚坦", "2026-09-12 18:00", "七丘", "[巡游天国]的入口——三王峰集合")])
    await chat.say("菲比", "ddl", quote=notice)
    await chat.say("菲比", "ddl确认", quote=chat.last_bot())
    scenes.append(
        scene(
            "转发的通知也能记",
            chat,
            "引用群里的通知（或者合并转发的聊天记录），发 ddl，尤诺会从里面整理出分类、截止、方式和说明。",
            "原文里没写的东西不会编出来。",
        )
    )

    battle = index_of("决战利维亚坦")
    chat = Chat()
    await chat.say("守岸人", "ddl列表")
    card = await chat.say("守岸人", f"ddl#{battle}")
    await chat.say("守岸人", "ddl 说明 黑海岸会派人支援", quote=card + 1)
    await chat.say("守岸人", "ddl确认", quote=chat.last_bot())
    scenes.append(
        scene(
            "引用卡片，就不用写编号",
            chat,
            f"ddl列表 看全部，ddl#{battle} 调出那一条的卡片。之后引用这张卡片回复要改的内容，不用再写 #{battle}。",
            "改动先出草稿给你看前后对比，引用草稿回复 ddl确认 才写进看板。",
        )
    )

    chat = Chat()
    notice = chat.quote_note(
        "珂莱塔",
        "莫塔里商会通知：下周一前提交新品样单；下周三前把拉古那分店的账目交给管家；下周五晚上8点在埃弗拉德金库办新品发布会。",
    )
    model_says(
        [
            item("提交新品样单", "2026-09-14 23:59", "莫塔里商会"),
            item("拉古那分店账目", "2026-09-16 23:59", "莫塔里商会", "交给管家"),
            item("新品发布会", "2026-09-18 20:00", "莫塔里商会", "埃弗拉德金库"),
        ]
    )
    await chat.say("卡提希娅", "ddl", quote=notice)
    await chat.say("卡提希娅", "ddl 第3条 说明 记得带邀请函", quote=chat.last_bot())
    await chat.say("卡提希娅", "ddl删1", quote=chat.last_bot())
    await chat.say("卡提希娅", "ddl确认", quote=chat.last_bot())
    scenes.append(
        scene(
            "一条通知好几件事",
            chat,
            "一条通知里有几件事，草稿就列几条。引用草稿回复「ddl 第3条 …」改某一条，「ddl删1」去掉某一条。",
            "每次改完都会给你一份新的草稿，引用最新那份确认。",
        )
    )

    chat = Chat()
    model_says([item("和达妮娅去观鸟", "2026-09-12 14:00")])
    await chat.say("西格莉卡", "ddl 周六下午2点 和达妮娅去观鸟")
    tid = chat.messages[chat.last_bot()]["text"].split("草稿 T", 1)[1].split("（", 1)[0]
    await chat.say("达妮娅", f"ddl取消T{tid}")
    await chat.say("西格莉卡", f"ddl确认T{tid}")
    scenes.append(
        scene(
            "不引用时，写一次编号",
            chat,
            f"不方便引用的时候，把编号写出来：草稿用 T 编号（ddl确认T{tid}），看板上的用 # 编号（ddl#12）。",
            "草稿只有发起人自己能确认或取消，别人发了也没用。",
        )
    )

    moning, losela = index_of("海维夏探测器"), index_of("入学考核")
    chat = Chat()
    await chat.say("琳奈", f"ddl合并#{moning}#{losela}")
    await chat.say("琳奈", "ddl确认", quote=chat.last_bot())
    await chat.say("琳奈", "ddl撤销")
    scenes.append(
        scene(
            "合并和撤销",
            chat,
            f"看板上早就记着莫宁教授和洛瑟菈校长交代的两件事。琳奈手滑，把 #{moning} 和 #{losela} 合成了一条——",
            "做错了就 ddl撤销，撤回自己上一次操作，24 小时内有效。真要合并时，ddl合并 后面还可以跟新名字。",
        )
    )

    back = index_of("追月节")
    chat = Chat()
    card = await chat.say("今汐", f"ddl#{back}")
    await chat.say("今汐", "ddl删", quote=card + 1)
    await chat.say("今汐", "ddl确认删除", quote=chat.last_bot())
    scenes.append(
        scene(
            "删除",
            chat,
            "漂泊者提前回来了，原定追月节那条不需要了：引用看板卡片回复 ddl删，会先出一份删除草稿；",
            "再引用它回复 ddl确认删除，才真的删掉。",
        )
    )

    chat = Chat()
    await chat.say("小爱", "@尤诺 最近有什么要办的？")
    scenes.append(
        scene(
            "直接问尤诺",
            chat,
            "开了 GSCore 的 AI 功能就能直接问。尤诺会查看板、发出清单；也能帮你起草，",
            "但不会替你确认、删除或撤销，起草出来的草稿还是要本人确认。",
        )
    )

    configure("homework")
    chat = Chat()
    model_says([])
    await chat.say("达妮娅", "ddl 今晚 吃小蛋糕")
    await chat.say("小爱", "ddl 明天 去罗伊冰原抓小海豹")
    numbers = "、".join(HOMEWORK.split())
    model_says([item("第三次作业", "2026-09-18 23:59", "共鸣频率学", "学部信箱", f"习题 {numbers}")])
    await chat.say("西格莉卡", f"ddl 共鸣频率学 第三次作业 9/18 23:59 交到学部信箱 习题{HOMEWORK}")
    scenes.append(
        scene(
            "作业模式（课程群）",
            chat,
            "星炬学院的课程群把 mode 改成 homework：只记作业，叫法变成作业、课程、平台、要求，不是作业的事都会被拒绝。",
            "两种模式写进看板的条目可以通用，随时可以切换。",
        )
    )
    configure("general")
    return scenes


# ---------------------------------------------------------------- 画图


def find_font(family: str) -> tuple[str, int]:
    out = subprocess.run(["fc-match", "-f", "%{file}\t%{index}", family], capture_output=True, text=True).stdout
    path, _, index = out.partition("\t")
    return path, int(index or 0)


CJK = find_font("Noto Sans CJK SC")
CJK_BOLD = find_font("Noto Sans CJK SC:bold")
EMOJI = find_font("Noto Color Emoji")[0]
W = 1080
BG, BUBBLE, BOT_BUBBLE = (242, 243, 245), (255, 255, 255), (232, 243, 255)
INK, GREY, LINE = (31, 35, 40), (120, 125, 135), (222, 225, 230)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path, index = CJK_BOLD if bold else CJK
    return ImageFont.truetype(path, size, index=index)


_EMOJI_CACHE: dict[str, Image.Image] = {}


def emoji_image(cluster: str, size: int) -> Image.Image:
    key = f"{cluster}@{size}"
    if key not in _EMOJI_CACHE:
        big = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
        ImageDraw.Draw(big).text((10, 10), cluster, font=ImageFont.truetype(EMOJI, 109), embedded_color=True)
        box = big.getbbox() or (0, 0, 1, 1)
        _EMOJI_CACHE[key] = big.crop(box).resize((size, size), Image.LANCZOS)
    return _EMOJI_CACHE[key]


def clusters(text: str) -> list[tuple[str, bool]]:
    """拆成（字，是不是 emoji）；emoji 后面跟着的 U+FE0F 并进同一个字。"""
    out: list[tuple[str, bool]] = []
    for ch in text:
        if ch == "️" and out:
            out[-1] = (out[-1][0] + ch, True)
            continue
        cp = ord(ch)
        out.append((ch, cp >= 0x1F000 or cp in (0x2754, 0x26A0, 0x270F)))
    return out


# 中文排版的避头尾：这些标点不放在行首（宁可稍微超出一点），这些不放在行尾
_NO_LINE_START = set("，。、；：？！）」』】》〉,.;:?!)～…")
_NO_LINE_END = set("（「『【《〈(")
_WORD = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:/.#@_-+=?&%")


def tokens(para: str) -> list[list[tuple[str, bool]]]:
    """折行的最小单位：连在一起的英文、数字和网址里的符号算一个整体，别的字一个一个来。"""
    out: list[list[tuple[str, bool]]] = []
    for ch, is_emoji in clusters(para):
        if not is_emoji and ch in _WORD and out and out[-1][-1][0] in _WORD:
            out[-1].append((ch, is_emoji))
        else:
            out.append([(ch, is_emoji)])
    return out


def wrap(text: str, f: ImageFont.FreeTypeFont, width: int) -> list[list[tuple[str, bool]]]:
    size = f.size

    def length(chars: list[tuple[str, bool]]) -> float:
        return sum(size * 1.15 if e else f.getlength(c) for c, e in chars)

    lines: list[list[tuple[str, bool]]] = []
    for para in text.split("\n"):
        line: list[tuple[str, bool]] = []
        x = 0.0
        for tok in tokens(para):
            w = length(tok)
            if line and x + w > width and tok[0][0] not in _NO_LINE_START:
                # 左括号这类不留在行尾，跟着下一个字换行
                carry = []
                while line and line[-1][0] in _NO_LINE_END:
                    carry.insert(0, line.pop())
                lines.append(line)
                line, x = carry, length(carry)
            if w > width:  # 一整串比一行还长，只好拆开
                for ch in tok:
                    cw = length([ch])
                    if line and x + cw > width:
                        lines.append(line)
                        line, x = [], 0.0
                    line.append(ch)
                    x += cw
                continue
            line.extend(tok)
            x += w
        lines.append(line)
    return lines


def draw_lines(img: Image.Image, lines, xy: tuple[int, int], f, fill, line_h: int) -> None:
    d = ImageDraw.Draw(img)
    x0, y = xy
    for line in lines:
        x = float(x0)
        for ch, is_emoji in line:
            if is_emoji:
                e = emoji_image(ch, int(f.size * 1.05))
                img.paste(e, (int(x), y + int(line_h * 0.12)), e)
                x += f.size * 1.15
            else:
                d.text((x, y), ch, font=f, fill=fill)
                x += f.getlength(ch)
        y += line_h


AVATARS = Path(__file__).resolve().parent / "avatars"


def avatar(who: str, size: int) -> Image.Image:
    """尤诺用插件自带的图标；其他人用 avatars/ 里的头像（出处见 avatars/sources.json），没有就画首字色块。"""
    path = ROOT / "plugin/IunoDDL/IunoDDL/iunoddl_help/icons/plugin.png" if who == "尤诺" else AVATARS / f"{who}.png"
    if path.is_file():
        icon = Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
    else:
        icon = Image.new("RGBA", (size, size), PEOPLE[who][1] + (255,))
        d = ImageDraw.Draw(icon)
        d.text((size / 2, size / 2), who[0], font=font(int(size * 0.45), bold=True), fill=(255, 255, 255), anchor="mm")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(icon, (0, 0), mask)
    return out


def render_message(msg: dict) -> Image.Image:
    f, small = font(30), font(24)
    line_h, pad, av = 44, 22, 76
    max_text = W - 40 - av - 24 - 2 * pad - 60
    body = wrap(msg["text"], f, max_text)
    quote_lines = []
    if msg["quote"]:
        q = msg["quote"]
        first = q["text"].split("\n", 1)[0]
        first = first if len(first) <= 26 else first[:25] + "…"
        quote_lines = wrap(f"{q['who']}：{first}", small, max_text - 24)[:2]
    text_w = max(
        [sum(small.size * 1.15 if e else small.getlength(c) for c, e in ln) for ln in quote_lines]
        + [sum(f.size * 1.15 if e else f.getlength(c) for c, e in ln) for ln in body]
        + [40]
    )
    quote_h = len(quote_lines) * 34 + 20 if quote_lines else 0
    bw = int(text_w) + 2 * pad
    bh = len(body) * line_h + 2 * pad - 8 + quote_h
    h = 40 + bh + 26
    img = Image.new("RGB", (W, h), BG)
    a = avatar(msg["who"], av)
    img.paste(a, (40, 8), a)
    d = ImageDraw.Draw(img)
    d.text((40 + av + 24, 6), msg["who"], font=small, fill=GREY)
    bx, by = 40 + av + 24, 40
    color = BOT_BUBBLE if msg["who"] == "尤诺" else BUBBLE
    d.rounded_rectangle((bx, by, bx + bw, by + bh), radius=18, fill=color)
    y = by + pad - 4
    if quote_lines:
        d.rounded_rectangle((bx + pad - 8, y - 4, bx + bw - pad + 8, y + quote_h - 16), radius=10, fill=(236, 238, 241))
        draw_lines(img, quote_lines, (bx + pad + 4, y + 2), small, GREY, 34)
        y += quote_h
    draw_lines(img, body, (bx + pad, y), f, INK, line_h)
    return img


def render_scene(no: int, scene: dict) -> Image.Image:
    parts = [render_message(m) for m in scene["chat"].messages]
    title_f, note_f = font(40, bold=True), font(27)
    notes = [ln for n in scene["notes"] for ln in wrap(n, note_f, W - 100)]
    head_h = 60 + 56 + len(notes) * 40 + 30
    foot = (
        "示例：对话里的事件、课程都是编的；尤诺的回复由插件代码实际生成，从通知里解析出什么取决于你配置的模型，这里是预设的。"
        "头像取自呜哇小站的表情包（CC BY-NC-SA 4.0），出处见 docs/examples/avatars/sources.json。"
    )
    foot_lines = wrap(foot, font(21), W - 100)
    total = head_h + 20 + sum(p.height for p in parts) + 20 + len(foot_lines) * 30 + 30
    page = Image.new("RGB", (W, total), BG)
    d = ImageDraw.Draw(page)
    d.rectangle((0, 0, W, head_h), fill=(255, 255, 255))
    d.text((50, 50), f"{no}. {scene['title']}", font=title_f, fill=(59, 145, 93))
    draw_lines(page, notes, (50, 120), note_f, INK, 40)
    d.line((0, head_h, W, head_h), fill=LINE, width=2)
    y = head_h + 20
    for p in parts:
        page.paste(p, (0, y))
        y += p.height
    draw_lines(page, foot_lines, (50, y + 20), font(21), GREY, 30)
    return page


def render_cover(scenes: list[dict]) -> Image.Image:
    title_f, f = font(56, bold=True), font(30)
    lines = [
        "群里发「ddl」加一句话，尤诺把要办的事整理成草稿，确认后写进看板。",
        "",
        "最重要的一条：引用尤诺发的卡片回复时，不用写编号；",
        "只有不引用的时候，才需要写一次编号（草稿 T3，看板 #12）。",
        "",
        "目录",
        *[f"{i}. {s['title']}" for i, s in enumerate(scenes, 1)],
        "",
        "完整说明见仓库里的 docs/使用教程.md。",
    ]
    wrapped = [ln for t in lines for ln in (wrap(t, f, W - 120) if t else [[]])]
    h = 260 + len(wrapped) * 48 + 80
    page = Image.new("RGB", (W, h), (255, 255, 255))
    a = avatar("尤诺", 120)
    page.paste(a, (60, 60), a)
    d = ImageDraw.Draw(page)
    d.text((210, 70), "DDL 小本本使用示例", font=title_f, fill=(59, 145, 93))
    d.text((212, 145), "github.com/acoinfan/iunoddl-kit", font=font(26), fill=GREY)
    draw_lines(page, wrapped, (60, 240), f, INK, 48)
    return page


def main() -> None:
    scenes = asyncio.run(build_scenes())
    OUT_IMAGES.mkdir(parents=True, exist_ok=True)
    for old in OUT_IMAGES.glob("示例-*.png"):
        old.unlink()
    pages = [render_cover(scenes)]
    pages[0].save(OUT_IMAGES / "示例-00-目录.png", optimize=True)
    for i, scene in enumerate(scenes, 1):
        page = render_scene(i, scene)
        page.save(OUT_IMAGES / f"示例-{i:02d}-{scene['title']}.png", optimize=True)
        pages.append(page)
    buf = io.BytesIO()
    # 时间写死成示例里的那天，PDF 每次生成都一模一样，不会多出无意义的改动
    pages[0].save(
        buf,
        format="PDF",
        save_all=True,
        append_images=pages[1:],
        resolution=144,
        title="DDL 小本本使用示例",
        creationDate=NOW.utctimetuple(),
        modDate=NOW.utctimetuple(),
    )
    OUT_PDF.write_bytes(buf.getvalue())
    print(f"生成了 {len(scenes)} 张示例图和 {OUT_PDF.relative_to(ROOT)}（{len(pages)} 页）")


if __name__ == "__main__":
    main()
