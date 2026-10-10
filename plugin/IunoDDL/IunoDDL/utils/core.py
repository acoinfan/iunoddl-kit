"""不依赖框架的纯逻辑：数据结构、日期解析、文字展示。可以单独拿出来测试。"""

import re
import random
import calendar
from typing import Literal, Optional
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from dataclasses import field, dataclass

TZ = ZoneInfo("Asia/Shanghai")
WEEKDAYS = "一二三四五六日"
DRAFT_TTL = 1800
DRAFT_MAX_PER_USER = 5
FIELD_NAMES = ("标题", "作业名", "课程", "截止", "平台", "要求")

# 机器人在回复里的自称，对应配置项 bot_name；store.load_config 每次读配置时同步过来
_bot_name = "尤诺"


def set_bot_name(name: str) -> None:
    global _bot_name
    _bot_name = name.strip() or "尤诺"


def bot_name() -> str:
    return _bot_name


def board_line(board_url: str) -> list[str]:
    """回复末尾的「看板：网址」；没配 board_url 就不写。"""
    return [f"看板：{board_url}"] if board_url else []


@dataclass
class DDLItem:
    course: str
    name: str
    due: Optional[datetime]
    platform: str
    detail: str
    # 确认后要更新的看板任务；None 表示新建
    target_id: Optional[int] = None
    target_index: Optional[int] = None
    target_title: str = ""
    # 去重命中时看板上原来的样子，用来显示「确认后会改什么」
    target_before: Optional["DDLItem"] = None

    @property
    def title(self) -> str:
        if self.course and self.name:
            return f"{self.course} · {self.name}"
        return self.name or self.course or "（没有名字的作业）"


@dataclass
class Draft:
    kind: Literal["new", "edit", "delete", "merge"]
    owner: str
    owner_name: str
    created: float
    items: list[DDLItem] = field(default_factory=list)
    source: str = ""
    # edit / delete 草稿指向的看板任务
    task_id: Optional[int] = None
    task_index: Optional[int] = None
    before: Optional[DDLItem] = None
    # 临时编号 T<tid>，确认 / 取消 / 修改都要写明
    tid: int = 0
    # 起草时看板任务的最后修改时间：task_id → updated；确认时对不上就说明被别人改过
    stamps: dict[int, str] = field(default_factory=dict)
    # merge 草稿：确认后并进 task_id、然后删掉的作业 (task_id, index, title)
    merged: list[tuple[int, int, str]] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"T{self.tid}"

    def summary(self) -> str:
        if self.kind == "new":
            names = "、".join(short(it.title, 14) for it in self.items[:3])
            more = f" 等 {len(self.items)} 条" if len(self.items) > 3 else ""
            return f"新建 {names}{more}"
        assert self.before is not None
        if self.kind == "merge":
            others = "、".join(f"#{idx}" for _, idx, _ in self.merged)
            return f"把 {others} 合并进 #{self.task_index}「{short(self.items[0].title, 14)}」"
        verb = "修改" if self.kind == "edit" else "删除"
        return f"{verb} #{self.task_index}「{short(self.before.title, 16)}」"


# ---------------------------------------------------------------- 合并


def merge_items(items: list[DDLItem], name: str = "") -> DDLItem:
    """几条作业合成一条：课程取共同的，截止取最早的，各部分的要求按顺序保留。"""
    assert len(items) >= 2
    courses = [it.course for it in items if it.course]
    course = courses[0] if courses else ""
    names = [it.name or it.title for it in items]
    if not name:
        joined = "；".join(names)
        name = joined if len(joined) <= 30 else f"{short(names[0], 16)} 等 {len(items)} 项"
    dues = [it.due for it in items if it.due is not None]
    due = min(dues) if dues else None
    platforms: list[str] = []
    for it in items:
        for p in re.split(r"[、,，/]", it.platform):
            if p.strip() and p.strip() not in platforms:
                platforms.append(p.strip())
    differ = len({it.due for it in items if it.due is not None}) > 1
    parts: list[str] = []
    for it, nm in zip(items, names):
        head = f"【{nm}】"
        if differ and it.due is not None:
            head += f"（原截止：{fmt_due(it.due, datetime.now(TZ), False)}）"
        parts.append(head + (it.detail or ""))
    return DDLItem(course, name, due, "、".join(platforms), "\n".join(parts))


# ---------------------------------------------------------------- 日期解析

_CN_WD = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6, "七": 6}
_RE_YMD = re.compile(r"(?:(\d{4})\s*[-/.年]\s*)?(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*[日号]?")
_RE_REL = re.compile(r"(今天|今晚|今日|明天|明晚|明日|后天)")
_RE_WEEK = re.compile(r"(下下|下|本|这)?\s*(?:周|星期|礼拜)\s*([一二三四五六日天七1-7])")
_RE_TIME = re.compile(
    r"(凌晨|早上|上午|中午|下午|傍晚|晚上|今晚|明晚)?\s*(\d{1,2})\s*(?::\s*(\d{1,2})|[点时]\s*(半|\d{1,2})?\s*分?)"
)


def _valid_day(y: int, m: int, d: int) -> bool:
    return 1 <= m <= 12 and 1 <= d <= calendar.monthrange(y, m)[1]


def parse_due(text: str, now: datetime) -> Optional[datetime]:
    """解析「10/18 23:59」「下周五晚上8点」这类常见写法；解析不了返回 None（交给模型）。"""
    s = text.strip().replace("：", ":")
    today = now.date()
    day: Optional[date] = None
    rest = s
    m = _RE_YMD.search(s)
    if m:
        y = int(m.group(1)) if m.group(1) else today.year
        mo, d = int(m.group(2)), int(m.group(3))
        if not _valid_day(y, mo, d):
            return None
        day = date(y, mo, d)
        if not m.group(1) and day < today - timedelta(days=60) and _valid_day(y + 1, mo, d):
            day = date(y + 1, mo, d)
        rest = s[: m.start()] + " " + s[m.end() :]
    else:
        m = _RE_REL.search(s)
        if m:
            offset = {"今": 0, "明": 1, "后": 2}[m.group(1)[0]]
            day = today + timedelta(days=offset)
        else:
            m = _RE_WEEK.search(s)
            if m:
                ch = m.group(2)
                wd = int(ch) - 1 if ch.isdigit() else _CN_WD[ch]
                cur = today.weekday()
                prefix = m.group(1) or ""
                if prefix == "下下":
                    delta = 14 - cur + wd
                elif prefix == "下":
                    delta = 7 - cur + wd
                elif prefix in ("本", "这"):
                    delta = wd - cur
                else:
                    delta = (wd - cur) % 7
                day = today + timedelta(days=delta)
        if m:
            rest = s[: m.start()] + " " + s[m.end() :]
    if day is None:
        return None
    hour, minute = 23, 59
    t = _RE_TIME.search(rest)
    if t:
        hour = int(t.group(2))
        if t.group(3) is not None:
            minute = int(t.group(3))
        elif t.group(4) == "半":
            minute = 30
        elif t.group(4):
            minute = int(t.group(4))
        else:
            minute = 0
        period = t.group(1) or ""
        if s.find("晚") >= 0 and not period:
            period = "晚上"
        if period in ("下午", "傍晚", "晚上", "今晚", "明晚") and hour < 12:
            hour += 12
        elif period == "中午" and hour < 11:
            hour += 12
        if hour == 24 and minute == 0:
            day += timedelta(days=1)
            hour = 0
        if hour > 23 or minute > 59:
            return None
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


DATE_HINT = re.compile(
    r"\d{1,2}\s*[月/.\-]\s*\d{1,2}|(?:周|星期|礼拜)[一二三四五六日天]|今天|今晚|明天|明晚|后天|下周|"
    r"\d{1,2}\s*[:：点]|截止|截至|deadline|due",
    re.IGNORECASE,
)


def looks_like_ddl(text: str) -> bool:
    return bool(DATE_HINT.search(text))


# ---------------------------------------------------------------- 编号与字段修改

_RE_FORMAL = re.compile(r"^(?:#|ddl-?)\s*(\d+)$", re.IGNORECASE)
_RE_TEMP = re.compile(r"^[Tt]\s*(\d+)$")
_RE_DRAFT = re.compile(r"^(\d+)$")


def parse_ref(token: str) -> tuple[Literal["formal", "temp", "draft", "bad"], int]:
    """#12 / DDL-12 → 看板编号；T3 → 草稿临时编号；3 → 草稿里的第几条。"""
    t = token.strip()
    m = _RE_TEMP.match(t)
    if m:
        return "temp", int(m.group(1))
    m = _RE_FORMAL.match(t)
    if m:
        return "formal", int(m.group(1))
    m = _RE_DRAFT.match(t)
    if m:
        return "draft", int(m.group(1))
    return "bad", 0


_FIELD_ALIASES = {
    "标题": "标题",
    "名字": "标题",
    "名称": "标题",
    "作业名": "作业名",
    "作业名称": "作业名",
    "作业": "作业名",
    "课程": "课程",
    "课": "课程",
    "截止": "截止",
    "截止时间": "截止",
    "ddl": "截止",
    "DDL": "截止",
    "时间": "截止",
    "平台": "平台",
    "提交": "平台",
    "提交平台": "平台",
    "要求": "要求",
    "备注": "要求",
    "详情": "要求",
    "说明": "要求",
}
_RE_FIELD = re.compile(
    r"(?:^|\s)(" + "|".join(sorted(map(re.escape, _FIELD_ALIASES), key=len, reverse=True)) + r")\s*[:：=]?\s*"
)


def parse_field_edits(text: str) -> Optional[dict[str, str]]:
    """「截止 10/18 23:59 平台 学习通」→ {"截止": "...", "平台": "..."}；不是这种格式返回 None。"""
    s = text.strip()
    matches = list(_RE_FIELD.finditer(s))
    if not matches or matches[0].start() != 0:
        return None
    out: dict[str, str] = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(s)
        value = s[m.end() : end].strip()
        if not value:
            return None
        out[_FIELD_ALIASES[m.group(1)]] = value
    return out


def apply_field_edits(item: DDLItem, edits: dict[str, str], now: datetime) -> Optional[str]:
    """把字段修改套到 item 上；成功返回 None，有字段解析不了时返回出错的字段名。"""
    if "截止" in edits:
        due = parse_due(edits["截止"], now)
        if due is None:
            return "截止"
    if "标题" in edits:
        item.course = ""
        item.name = edits["标题"]
    if "作业名" in edits:
        item.name = edits["作业名"]
    if "课程" in edits:
        item.course = edits["课程"]
    if "截止" in edits:
        item.due = parse_due(edits["截止"], now)
    if "平台" in edits:
        item.platform = "" if edits["平台"] in ("无", "空", "没有") else edits["平台"]
    if "要求" in edits:
        item.detail = edits["要求"]
    return None


# ---------------------------------------------------------------- 展示


def fmt_due(due: Optional[datetime], now: datetime, with_left: bool = True) -> str:
    if due is None:
        return "还不知道"
    d = due.astimezone(TZ)
    text = f"{d.month}月{d.day}日 周{WEEKDAYS[d.weekday()]} {d:%H:%M}"
    if d.year != now.year:
        text = f"{d.year}年" + text
    if not with_left:
        return text
    days = (d.date() - now.date()).days
    if d < now:
        left = "已经过了" if days == 0 else f"已经过了 {-days} 天"
    elif days == 0:
        left = "就是今天"
    elif days == 1:
        left = "明天"
    else:
        left = f"还有 {days} 天"
    return f"{text}（{left}）"


def short(text: str, limit: int = 80) -> str:
    t = " ".join(text.split())
    return t if len(t) <= limit else t[: limit - 1] + "…"


def item_lines(i: int, it: DDLItem, now: datetime, label: str) -> list[str]:
    lines = ["", f"{i}. {it.title}", f"   截止：{fmt_due(it.due, now)}"]
    if it.platform:
        lines.append(f"   平台：{it.platform}")
    if it.detail:
        lines.append(f"   要求：{short(it.detail)}")
    if it.due is None:
        lines.append(f"   ⚠ 没找到截止时间，引用这条回复：ddl 第{i}条截止10/18 23:59")
    if it.target_id is not None:
        lines.append(f"   ↻ 看板上已有 #{it.target_index}「{short(it.target_title, 30)}」，确认后会更新它：")
        changes = diff_lines(it.target_before, it, now) if it.target_before is not None else []
        lines += [f"     {c}" for c in changes] or ["     （内容一样，只把这次的通知原文补进去）"]
        lines.append(f"     想另建一条，引用这条回复：ddl 第{i}条新建")
    return lines


_DRAFT_HEAD = (
    "整理好了，自己核对一下，错了可别赖{name}。",
    "哼，这点通知还难不倒{name}。看看对不对？",
    "帮你理清楚了，确认之前好好看一眼哦。",
)
_EDIT_HEAD = (
    "改好了，再核对一眼～",
    "这样总对了吧？再看一眼。",
    "按你说的改了，自己看看。",
)
_DONE_HEAD = (
    "记好了，这下可忘不掉了吧～",
    "写进看板了。到时候没交，可别说{name}没提醒。",
    "好啦，都记上了。……顺手而已，不用谢。",
)


def pick(options: tuple[str, ...]) -> str:
    return random.choice(options).format(name=_bot_name)


def render_new_draft(d: Draft, now: datetime, edited: bool = False) -> str:
    t = d.label
    lines = [pick(_EDIT_HEAD if edited else _DRAFT_HEAD)]
    lines += ["", f"📝 草稿 {t}（{d.owner_name} · 30 分钟内有效）"]
    for i, it in enumerate(d.items, 1):
        lines.extend(item_lines(i, it, now, t))
    first = "ddl 截止周五晚上" if len(d.items) == 1 else "ddl 第1条截止周五晚上"
    lines += [
        "",
        "引用这条消息回复：",
        "· ddl确认 → 写进看板",
        f"· {first} → 修改",
    ]
    if len(d.items) > 1:
        lines.append(f"· ddl删{len(d.items)} → 去掉第 {len(d.items)} 条")
        lines.append("· ddl合并 → 拆错了的话合成一条（也可以 ddl合并1 2）")
    lines += [
        "· ddl取消 → 不要了",
        f"（不引用的话要写编号，比如 ddl确认{t}）",
    ]
    return "\n".join(lines)


def diff_lines(before: DDLItem, after: DDLItem, now: datetime) -> list[str]:
    out: list[str] = []
    if before.title != after.title:
        out.append(f"标题：{before.title} → {after.title}")
    if before.due != after.due:
        out.append(f"截止：{fmt_due(before.due, now, False)} → {fmt_due(after.due, now)}")
    if before.platform != after.platform:
        out.append(f"平台：{before.platform or '无'} → {after.platform or '无'}")
    if before.detail != after.detail:
        out.append(f"要求：{short(before.detail, 40) or '无'} → {short(after.detail, 60) or '无'}")
    return out


def render_edit_draft(d: Draft, now: datetime) -> str:
    assert d.before is not None
    t = d.label
    changes = diff_lines(d.before, d.items[0], now)
    lines = ["改好了，先给你看看前后对比。"]
    lines += ["", f"✏️ 草稿 {t}：修改 #{d.task_index}「{short(d.before.title, 30)}」（{d.owner_name}）", ""]
    lines += [f"· {c}" for c in changes] or ["（还没有改动）"]
    lines += [
        "",
        "引用这条消息回复：",
        "· ddl确认 → 写进看板",
        "· ddl 平台学习通 → 接着改",
        "· ddl取消 → 不改了",
        f"（不引用的话要写编号，比如 ddl确认{t}）",
    ]
    return "\n".join(lines)


def render_delete_draft(d: Draft, now: datetime) -> str:
    assert d.before is not None
    t = d.label
    lines = [
        f"🗑️ 草稿 {t}：真的要删掉 #{d.task_index}「{d.before.title}」吗？",
        f"截止：{fmt_due(d.before.due, now)}",
        "",
        "删了看板上就没有了，大家都看不到哦。",
        "",
        "引用这条消息回复：",
        "· ddl确认删除 → 真的删掉",
        "· ddl取消 → 不删了",
        f"（不引用的话要写编号，比如 ddl确认删除{t}）",
    ]
    return "\n".join(lines)


def render_merge_draft(d: Draft, now: datetime) -> str:
    assert d.before is not None
    t = d.label
    it = d.items[0]
    others = "、".join(f"#{idx}" for _, idx, _ in d.merged)
    lines = [f"🔗 草稿 {t}：把 {others} 合并进 #{d.task_index}（{d.owner_name}）", ""]
    lines += [f"· #{d.task_index} {short(d.before.title, 34)}"]
    lines += [f"· #{idx} {short(title, 34)}" for _, idx, title in d.merged]
    lines += ["", "合并以后：", f"   {it.title}", f"   截止：{fmt_due(it.due, now)}"]
    if it.platform:
        lines.append(f"   平台：{it.platform}")
    if it.detail:
        lines.append(f"   要求：{short(it.detail, 160)}")
    lines += [
        "",
        f"确认后 {others} 会删掉，内容和原始通知都并进 #{d.task_index}；弄错了可以 ddl撤销。",
        "",
        "引用这条消息回复：",
        "· ddl确认 → 写进看板",
        "· ddl 作业名 第四章作业 → 改合并后的名字",
        "· ddl取消 → 不合并了",
        f"（不引用的话要写编号，比如 ddl确认{t}）",
    ]
    return "\n".join(lines)


def render_draft(d: Draft, now: datetime, edited: bool = False) -> str:
    if d.kind == "new":
        return render_new_draft(d, now, edited)
    if d.kind == "edit":
        return render_edit_draft(d, now)
    if d.kind == "merge":
        return render_merge_draft(d, now)
    return render_delete_draft(d, now)


_HELP = """📒 {name}的 DDL 小本本

小窍门：在手机上长按机器人发的卡片 →「回复」，就不用手打编号了。

【记作业】
ddl
　→ 先引用一条转发或通知，再发这个
ddl 数值分析 第三次作业 10/18 23:59 学习通
　→ 不引用，直接打字也行

【处理草稿】（引用草稿卡片回复，30 分钟内有效）
ddl确认
　→ 写进看板
ddl 截止周五晚上
　→ 修改；草稿里有好几条时写成 ddl 第1条截止周五
ddl 标题 计算机图形学2026---高老师课程群
　→ 替换完整标题，不保留原来的课程前缀
ddl 作业名 第二次作业
　→ 只改作业名，保留课程；只改课程可用 ddl 课程 数值分析
ddl删2
　→ 去掉第 2 条
ddl取消
　→ 这份不要了
ddl草稿
　→ 看看自己手上还有哪些草稿
不引用卡片时要写编号：ddl确认T3、ddl取消T3、ddl改T3 1截止周五

【改看板上的】
ddl#12
　→ 先调出作业卡片
引用作业卡片回复 ddl 要改的内容
　→ 比如 ddl 截止周五、ddl 要交PDF，生成修改草稿，再引用草稿 ddl确认
引用作业卡片回复 ddl删
　→ 生成删除草稿，再引用它回复 ddl确认删除
ddl撤销
　→ 撤回自己上一次操作（24 小时内）

【拆错了，合成一条】
引用草稿卡片回复 ddl合并
　→ 草稿里的几条合成一条；只合并其中几条写 ddl合并1 2
ddl合并#24#25#26
　→ 看板上已有的几条合成一条（并进第一条，其余删掉）；后面可以跟新名字，比如 ddl合并#24#25 第四章作业
　→ 都会先出草稿给你看，ddl确认 才写入，写错了能 ddl撤销

【查看】
ddl列表
　→ 所有没完成的作业
ddl#12
　→ 看一条的详情"""


def help_text(board_url: str) -> str:
    return "\n\n".join([_HELP.format(name=_bot_name), *board_line(board_url)])


def done_head() -> str:
    return pick(_DONE_HEAD)
