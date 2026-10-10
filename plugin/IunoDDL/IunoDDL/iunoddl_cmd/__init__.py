import re
import copy
import time
from typing import Optional
from datetime import datetime

import httpx

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event

from .service import (
    find_match,
    apply_draft,
    render_list,
    undo_record,
    task_to_item,
    known_courses,
    render_detail,
)
from ..utils.llm import edit_item, parse_notice
from ..utils.core import (
    TZ,
    DRAFT_TTL,
    DRAFT_MAX_PER_USER,
    Draft,
    DDLItem,
    short,
    bot_name,
    done_head,
    help_text,
    parse_ref,
    board_line,
    merge_items,
    render_draft,
    looks_like_ddl,
    apply_field_edits,
    parse_field_edits,
)
from ..utils.store import Config, UndoRecord, load_meta, load_undo, load_config, is_configured
from ..iunoddl_help import get_help_img
from ..utils.vikunja import Json, Vikunja, VikunjaError, jint, jstr

sv = SV("DDL记录", pm=6, priority=3, area="ALL")

# 临时编号 → 草稿。编号全局递增，重启后从 1 重新开始（草稿本来就只在内存里）
DRAFTS: dict[int, Draft] = {}
_next_tid = 1

_REF = r"(?:[Tt]\s*\d+|#\s*\d+|ddl-?\s*\d+|\d+)"
_RE_CONFIRM = re.compile(r"^(?:确认|确定)\s*(删除)?\s*(.*)$")
_RE_CANCEL = re.compile(r"^(?:取消|算了|放弃)\s*(.*)$")
_RE_EDIT = re.compile(rf"^改\s*({_REF})\s*(.*)$", re.S | re.I)
_RE_DEL = re.compile(rf"^(?:删除|删)\s*({_REF})?\s*(\d*)$", re.I)
_RE_UNDO = re.compile(r"^撤销\s*(\S*)$")
_RE_MERGE = re.compile(r"^合并\s*(.*)$", re.S)
_RE_MERGE_DRAFT = re.compile(r"^[Tt]\s*(\d+)\s*(.*)$", re.S)
_RE_TASK = re.compile(r"^(?:#|ddl-?)\s*(\d+)\s*(.*)$", re.S | re.I)
_RE_NEW = re.compile(r"^(?:记|新建|添加|新增)\s*")
# 草稿里第几条：「第1条截止周五」「1 截止周五」「1截止周五」
_RE_ITEM_NTH = re.compile(r"^第\s*(\d+)\s*条\s*(.*)$", re.S)
_RE_ITEM_SPACE = re.compile(r"^(\d+)\s+(.*)$", re.S)
_RE_ITEM_FIELD = re.compile(r"^(\d+)(?=标题|课程|截止|平台|要求|新建)(.*)$", re.S)
# 引用卡片时用来认编号的标记，和卡片文字对应
_RE_CARD_DRAFT = re.compile(r"草稿\s*T(\d+)")
_RE_CARD_TASK = re.compile(r"作业\s*#(\d+)")
_CARD_MARKS = ("DDL 小本本", "ddl确认", "ddl 确认")
_WORDS_CONFIRM = ("确认", "确定", "好", "ok", "OK", "可以")
_WORDS_CANCEL = ("取消", "算了", "放弃", "不要了")
_GLOBAL = ("列表", "清单", "全部", "list", "看板", "草稿", "暂存", "我的草稿", "stash")
_TASK_EDIT_HINT = (
    "改看板上的作业换新写法啦：先发 ddl#{n} 调出作业卡片，再引用那张卡片回复 ddl 要改的内容（比如 ddl 截止周五晚上）。"
)
_NODE_MARK = "[合并转发]"


def _sender_name(ev: Event) -> str:
    for key in ("card", "nickname"):
        if key in ev.sender:
            v = ev.sender[key]
            if isinstance(v, str) and v.strip():
                return v.strip()
    return str(ev.user_id)


def _is_admin(ev: Event, cfg: Config) -> bool:
    return str(ev.user_id) in cfg["admins"] or ev.user_pm <= 1


def _allowed(ev: Event, cfg: Config) -> bool:
    if ev.group_id:
        return str(ev.group_id) in cfg["allowed_groups"]
    return _is_admin(ev, cfg) or str(ev.user_id) in cfg["private_users"]


def _quoted(ev: Event) -> str:
    if ev.node:
        lines: list[str] = []
        for m in ev.node:
            if m.type == "text" and m.data is not None:
                t = str(m.data).strip()
                if t and t != _NODE_MARK:
                    lines.append(t)
            elif m.type == "image":
                lines.append("[图片]")
            elif m.type == "file":
                lines.append("[文件]")
        return "\n".join(lines).strip()
    if ev.reply:
        return "\n".join(x for x in str(ev.reply).splitlines() if x.strip() != _NODE_MARK).strip()
    return ""


def _api(cfg: Config) -> Vikunja:
    return Vikunja(cfg["vikunja_api"], cfg["vikunja_token"], cfg["project_id"], cfg["view_id"])


# ---------------------------------------------------------------- 草稿管理


def _purge() -> None:
    now = time.time()
    for tid in [t for t, d in DRAFTS.items() if now - d.created > DRAFT_TTL]:
        del DRAFTS[tid]


def _my_drafts(user: str) -> list[Draft]:
    _purge()
    return sorted((d for d in DRAFTS.values() if d.owner == user), key=lambda d: d.tid)


def _add_draft(d: Draft) -> Optional[Draft]:
    """登记草稿并分配编号；超过每人上限时挤掉最旧的一份并返回它。"""
    global _next_tid
    d.tid = _next_tid
    _next_tid += 1
    mine = _my_drafts(d.owner)
    dropped = mine[0] if len(mine) >= DRAFT_MAX_PER_USER else None
    if dropped is not None:
        del DRAFTS[dropped.tid]
    DRAFTS[d.tid] = d
    return dropped


def _drafts_hint(user: str, example: str) -> str:
    mine = _my_drafts(user)
    if not mine:
        return "你现在没有待确认的草稿哦（草稿 30 分钟没动会自动作废）。"
    lines = ["要引用草稿卡片回复，或者写上草稿编号才行，免得传错。你手上的草稿："]
    lines += [f"· {d.label}　{d.summary()}" for d in mine]
    lines.append(f"比如：{example.format(t=mine[-1].label)}")
    return "\n".join(lines)


async def _take_draft(bot: Bot, ev: Event, token: str, example: str) -> Optional[Draft]:
    """按「T3」取自己的草稿；编号没写、写错、不是自己的，都回提示并返回 None。"""
    user = str(ev.user_id)
    kind, n = parse_ref(token) if token else ("bad", 0)
    if kind != "temp":
        await bot.send(_drafts_hint(user, example))
        return None
    _purge()
    d = DRAFTS[n] if n in DRAFTS else None
    if d is None:
        await bot.send(f"草稿 T{n} 不存在，或者已经过期作废了。发 ddl 草稿 看看手上还有哪些。")
        return None
    if d.owner != user:
        await bot.send(f"草稿 T{n} 是 {d.owner_name} 的，只有本人能操作哦。")
        return None
    return d


def _with_dropped(text: str, dropped: Optional[Draft]) -> str:
    if dropped is None:
        return text
    return f"{text}\n\n（你手上草稿太多了，最旧的 {dropped.label}「{dropped.summary()}」已经作废）"


def _stamp(task: Json) -> str:
    return jstr(task, "updated")


# ---------------------------------------------------------------- 入口


@sv.on_command(("ddl", "DDL", "Ddl"), block=True)
async def ddl_entry(bot: Bot, ev: Event) -> None:
    cfg = load_config()
    if not _allowed(ev, cfg):
        return
    text = ev.text.strip()
    if text in ("帮助", "help", "?", "？"):
        await bot.send(await get_help_img(cfg["board_url"]))
        return
    if text in ("文字帮助", "帮助文字", "文字版帮助", "文字版"):
        await bot.send(help_text(cfg["board_url"]))
        return
    if not is_configured(cfg):
        await bot.send(f"看板的钥匙还没配好，{bot_name()}现在写不进去……让管理员检查一下 IunoDDL 的配置吧。")
        return
    try:
        await _dispatch(bot, ev, cfg, text)
    except (VikunjaError, httpx.HTTPError) as e:
        logger.exception(f"[IunoDDL] 看板接口出错：{e}")
        await bot.send(f"看板那边出错了：{short(str(e), 120)}\n等一会儿再试，还不行就找管理员看看。")


async def _dispatch(bot: Bot, ev: Event, cfg: Config, text: str) -> None:
    now = datetime.now(TZ)
    quoted = _quoted(ev)
    user = str(ev.user_id)

    if quoted and text not in _GLOBAL and not _RE_UNDO.match(text):
        m = _RE_CARD_DRAFT.search(quoted)
        if m:
            await _on_draft_card(bot, ev, cfg, f"T{m.group(1)}", text, now)
            return
        m = _RE_CARD_TASK.search(quoted)
        if m:
            await _on_task_card(bot, ev, cfg, int(m.group(1)), text, now)
            return
        marks = (*_CARD_MARKS, cfg["board_url"]) if cfg["board_url"] else _CARD_MARKS
        if any(mark in quoted for mark in marks):
            await bot.send(
                "这张卡片不能直接改哦。\n"
                "· 改作业：先发 ddl#编号 调出作业卡片，再引用它回复 ddl 要改的内容\n"
                "· 处理草稿：引用草稿卡片回复 ddl确认 / ddl取消"
            )
            return

    m = _RE_CONFIRM.match(text)
    if m:
        await _confirm(bot, ev, cfg, m.group(2), delete=bool(m.group(1)))
        return
    m = _RE_CANCEL.match(text)
    if m:
        await _cancel(bot, ev, m.group(1))
        return
    if text in ("草稿", "暂存", "我的草稿", "stash"):
        mine = _my_drafts(user)
        if not mine:
            await bot.send("你手上一份草稿都没有。")
        else:
            lines = ["你手上还没确认的草稿："] + [f"· {d.label}　{d.summary()}" for d in mine]
            lines.append(f"看某一份：ddl 草稿 {mine[-1].label}")
            await bot.send("\n".join(lines))
        return
    if text.startswith(("草稿", "暂存")):
        d = await _take_draft(bot, ev, text[2:].strip(), "ddl草稿{t}")
        if d is not None:
            await bot.send(render_draft(d, now))
        return
    m = _RE_UNDO.match(text)
    if m:
        await _undo(bot, ev, cfg, m.group(1))
        return
    m = _RE_MERGE.match(text)
    if m:
        await _merge(bot, ev, cfg, m.group(1).strip(), now)
        return
    if text in ("列表", "清单", "全部", "list", "看板"):
        await bot.send(render_list(await _api(cfg).list_tasks(), cfg["board_url"]))
        return
    m = _RE_TASK.match(text)
    if m and m.group(2).strip():
        await bot.send(_TASK_EDIT_HINT.format(n=m.group(1)))
        return
    kind, n = parse_ref(text)
    if kind == "formal":
        task = await _api(cfg).find_by_index(n)
        if task is None:
            await bot.send(f"看板上没有 #{n} 这一条哦，发 ddl 列表 看看编号？")
            return
        meta = load_meta()
        tid = str(jint(task, "id"))
        await bot.send(render_detail(task, meta[tid] if tid in meta else None, cfg["board_url"]))
        return
    if kind == "temp":
        d = await _take_draft(bot, ev, text, "ddl草稿{t}")
        if d is not None:
            await bot.send(render_draft(d, now))
        return
    m = _RE_EDIT.match(text)
    if m:
        await _edit(bot, ev, cfg, m.group(1), m.group(2).strip(), now)
        return
    m = _RE_DEL.match(text)
    if m:
        await _delete(bot, ev, cfg, m.group(1) or "", m.group(2), now)
        return
    if text.startswith(("改", "删")):
        await bot.send(
            "要改哪一份呀？\n· 草稿：引用草稿卡片回复，或者写编号 ddl改T3 1截止周五"
            "\n· 看板上的：先 ddl#编号 调出卡片，再引用卡片回复"
        )
        return
    if not text and not quoted:
        await bot.send(await get_help_img(cfg["board_url"]))
        return

    # 剩下的都当成新的作业通知
    body = _RE_NEW.sub("", text, count=1)
    if quoted:
        source, hint = quoted, body
    else:
        if not looks_like_ddl(body):
            # 「ddl 好多啊」这类闲聊，不打扰
            return
        source, hint = body, ""
    await _new(bot, ev, cfg, source, hint, now)


async def _new(bot: Bot, ev: Event, cfg: Config, source: str, hint: str, now: datetime) -> None:
    await bot.send(await prepare_new_draft(ev, cfg, source, hint, now))


async def prepare_new_draft(ev: Event, cfg: Config, source: str, hint: str, now: datetime) -> str:
    """只生成通知草稿；命令入口和 AI 工具复用，确认仍由独立命令处理。"""
    api = _api(cfg)
    tasks = await api.list_tasks()
    items = await parse_notice(source, hint, known_courses(tasks, load_meta()), now)
    if items is None:
        return f"唔，这段通知{bot_name()}没读明白……可以直接写成：ddl 数值分析 第三次作业 10/18 23:59 学习通"
    if not items:
        return "这里面……没找到需要交的作业呀。是不是转发错了？"
    metas = load_meta()
    stamps: dict[int, str] = {}
    for it in items:
        match = find_match(it, tasks)
        if match is not None:
            mid = jint(match, "id")
            before = task_to_item(match, metas[str(mid)] if str(mid) in metas else None)
            it.target_before = before
            # 新通知没提到的字段沿用看板上原来的，不清空
            it.due = it.due if it.due is not None else before.due
            it.platform = it.platform or before.platform
            it.detail = it.detail or before.detail
            it.target_id, it.target_index, it.target_title = mid, jint(match, "index"), jstr(match, "title")
            stamps[mid] = _stamp(match)
    d = Draft(
        kind="new",
        owner=str(ev.user_id),
        owner_name=_sender_name(ev),
        created=time.time(),
        items=items,
        source=source,
        stamps=stamps,
    )
    dropped = _add_draft(d)
    return _with_dropped(render_draft(d, now), dropped)


async def _modify(item: DDLItem, instruction: str, now: datetime) -> Optional[DDLItem]:
    edits = parse_field_edits(instruction)
    if edits is not None:
        new = copy.deepcopy(item)
        if apply_field_edits(new, edits, now) is None:
            return new
    return await edit_item(item, instruction, now)


async def _apply_edit(bot: Bot, d: Draft, idx: int, rest: str, now: datetime) -> None:
    await bot.send(await prepare_draft_edit(d, idx, rest, now))


async def prepare_draft_edit(d: Draft, idx: int, rest: str, now: datetime) -> str:
    """修改草稿内容，不写看板；调用方负责本人校验与条目范围检查。"""
    item = d.items[idx - 1]
    if rest in ("新建", "另建", "不合并") and d.kind == "new":
        if item.target_id is not None:
            d.stamps.pop(item.target_id, None)
        item.target_id, item.target_index, item.target_title, item.target_before = None, None, "", None
    else:
        new = await _modify(item, rest, now)
        if new is None:
            return f"这句话{bot_name()}没看懂……引用草稿换个说法试试？比如 ddl 第{idx}条截止10/18 23:59"
        d.items[idx - 1] = new
    d.created = time.time()
    return render_draft(d, now, edited=True)


def _split_item(rest: str) -> tuple[Optional[int], str]:
    """「第1条截止周五」「1 截止周五」「1截止周五」→ (1, "截止周五")；没写第几条 → (None, 原文)。"""
    for pattern in (_RE_ITEM_NTH, _RE_ITEM_SPACE, _RE_ITEM_FIELD):
        m = pattern.match(rest)
        if m:
            return int(m.group(1)), m.group(2).strip()
    if rest.isdigit():
        return int(rest), ""
    return None, rest


async def _edit(bot: Bot, ev: Event, cfg: Config, ref: str, rest: str, now: datetime) -> None:
    kind, n = parse_ref(ref)
    if kind == "formal":
        await bot.send(_TASK_EDIT_HINT.format(n=n))
        return
    if kind != "temp":
        await bot.send("要改哪一份呀？草稿要引用草稿卡片回复，或者写编号，比如 ddl改T3 1截止周五")
        return
    d = await _take_draft(bot, ev, ref, "ddl改{t} 1截止周五")
    if d is None:
        return
    if d.kind == "delete":
        await bot.send(f"{d.label} 是删除草稿，没什么可改的。不删了就引用它回复 ddl取消")
        return
    idx, rest = _split_item(rest)
    if idx is None:
        if len(d.items) > 1:
            await bot.send(
                f"{d.label} 里有 {len(d.items)} 条，要说改第几条，比如引用草稿回复：ddl 第1条{rest or '截止周五'}"
            )
            return
        idx = 1
    if not 1 <= idx <= len(d.items):
        await bot.send(f"{d.label} 里只有 {len(d.items)} 条，没有第 {idx} 条哦。")
        return
    if not rest:
        await bot.send(render_draft(d, now) + "\n\n想改什么？引用这条回复，比如：ddl 截止周五晚上")
        return
    await _apply_edit(bot, d, idx, rest, now)


async def _edit_task(bot: Bot, ev: Event, cfg: Config, n: int, rest: str, now: datetime) -> None:
    """引用作业卡片修改看板上的作业：生成（或接着改）一份修改草稿，确认后才写入。"""
    await bot.send(await prepare_task_edit(ev, cfg, n, rest, now))


async def prepare_task_edit(ev: Event, cfg: Config, n: int, rest: str, now: datetime) -> str:
    """生成或接着修改本人的任务修改草稿，不提交到看板。"""
    user = str(ev.user_id)
    for d in _my_drafts(user):
        if d.kind == "edit" and d.task_index == n:
            return await prepare_draft_edit(d, 1, rest, now)
    task = await _api(cfg).find_by_index(n)
    if task is None:
        return f"看板上已经没有 #{n} 这一条了，发 ddl列表 看看？"
    meta = load_meta()
    tid = jint(task, "id")
    before = task_to_item(task, meta[str(tid)] if str(tid) in meta else None)
    d = Draft(
        kind="edit",
        owner=user,
        owner_name=_sender_name(ev),
        created=time.time(),
        items=[copy.deepcopy(before)],
        task_id=tid,
        task_index=n,
        before=before,
        stamps={tid: _stamp(task)},
    )
    new = await _modify(d.items[0], rest, now)
    if new is None:
        return f"这句话{bot_name()}没看懂……换个说法试试？比如引用作业卡片回复：ddl 截止周五晚上、ddl 要交PDF"
    d.items[0] = new
    dropped = _add_draft(d)
    return _with_dropped(render_draft(d, now), dropped)


async def _on_draft_card(bot: Bot, ev: Event, cfg: Config, t: str, text: str, now: datetime) -> None:
    """引用了草稿卡片：编号从卡片里读，回复的内容决定确认、取消、去掉一条还是修改。"""
    if re.fullmatch(r"(?:确认|确定)\s*删除", text):
        await _confirm(bot, ev, cfg, t, delete=True)
        return
    if text in _WORDS_CONFIRM:
        await _confirm(bot, ev, cfg, t, delete=False)
        return
    if text in _WORDS_CANCEL:
        await _cancel(bot, ev, t)
        return
    if not text:
        d = await _take_draft(bot, ev, t, "ddl草稿{t}")
        if d is not None:
            await bot.send(render_draft(d, now))
        return
    m = _RE_MERGE.match(text)
    if m:
        await _merge_draft(bot, ev, t, m.group(1).strip(), now)
        return
    m = re.fullmatch(r"(?:删除|删|去掉)\s*第?\s*(\d+)\s*条?", text) or re.fullmatch(
        r"第?\s*(\d+)\s*条?\s*(?:不要了|删掉|去掉|删除|删)", text
    )
    if m:
        await _delete(bot, ev, cfg, t, m.group(1), now)
        return
    await _edit(bot, ev, cfg, t, text, now)


async def _on_task_card(bot: Bot, ev: Event, cfg: Config, n: int, text: str, now: datetime) -> None:
    """引用了作业卡片：回复「删」就起删除草稿，其余当成修改内容。"""
    if not text:
        await bot.send(f"引用了 #{n}，想改什么？比如引用卡片回复：ddl 截止周五晚上、ddl 要交PDF，或者 ddl删")
        return
    if text in ("删", "删除", "删掉"):
        await _delete(bot, ev, cfg, f"#{n}", "", now)
        return
    m = _RE_MERGE.match(text)
    if m:
        await _merge(bot, ev, cfg, f"#{n} {m.group(1).strip()}", now)
        return
    if text in _WORDS_CONFIRM or text in _WORDS_CANCEL:
        await bot.send(f"这是作业 #{n} 的卡片哦。确认或取消要引用草稿卡片（标着「草稿 T编号」的那条）回复。")
        return
    await _edit_task(bot, ev, cfg, n, text, now)


async def _delete(bot: Bot, ev: Event, cfg: Config, ref: str, item_no: str, now: datetime) -> None:
    kind, n = parse_ref(ref)
    if kind == "temp":
        d = await _take_draft(bot, ev, ref, "ddl 删 {t} 2")
        if d is None:
            return
        if d.kind != "new" or not item_no.isdigit():
            await bot.send(f"要去掉 {d.label} 里的某一条，引用草稿回复 ddl删2；整份不要了回复 ddl取消")
            return
        idx = int(item_no)
        if not 1 <= idx <= len(d.items):
            await bot.send(f"{d.label} 里只有 {len(d.items)} 条，没有第 {idx} 条哦。")
            return
        removed = d.items.pop(idx - 1)
        if removed.target_id is not None and all(it.target_id != removed.target_id for it in d.items):
            d.stamps.pop(removed.target_id, None)
        if not d.items:
            del DRAFTS[d.tid]
            await bot.send(f"{d.label} 删空了，这份就作废啦。")
            return
        d.created = time.time()
        await bot.send(render_draft(d, now, edited=True))
        return
    if kind != "formal":
        await bot.send(
            "要删哪一条呀？\n· 草稿里的某一条：引用草稿卡片回复 ddl删2"
            "\n· 看板上的作业：引用作业卡片回复 ddl删，或者 ddl删#12"
        )
        return
    task = await _api(cfg).find_by_index(n)
    if task is None:
        await bot.send(f"看板上没有 #{n} 这一条哦，发 ddl 列表 看看编号？")
        return
    meta = load_meta()
    tid = jint(task, "id")
    before = task_to_item(task, meta[str(tid)] if str(tid) in meta else None)
    d = Draft(
        kind="delete",
        owner=str(ev.user_id),
        owner_name=_sender_name(ev),
        created=time.time(),
        task_id=tid,
        task_index=n,
        before=before,
        stamps={tid: _stamp(task)},
    )
    dropped = _add_draft(d)
    await bot.send(_with_dropped(render_draft(d, now), dropped))


# ---------------------------------------------------------------- 合并

_MERGE_USAGE = (
    "要合并哪几条呀？\n"
    "· 草稿里拆开的几条：引用草稿卡片回复 ddl合并（只合并其中几条：ddl合并1 2）\n"
    "· 看板上已有的：ddl合并#24#25#26，后面可以跟新名字，比如 ddl合并#24#25 第四章作业"
)


async def _merge(bot: Bot, ev: Event, cfg: Config, rest: str, now: datetime) -> None:
    m = _RE_MERGE_DRAFT.match(rest)
    if m:
        await _merge_draft(bot, ev, f"T{m.group(1)}", m.group(2).strip(), now)
        return
    refs = [int(x) for x in re.findall(r"#\s*(\d+)", rest)]
    name = re.sub(r"#\s*\d+", " ", rest).strip(" ，,、和与及")
    if not refs and re.fullmatch(r"[\d\s,，、和与及]+", rest or "x"):
        refs, name = [int(x) for x in re.findall(r"\d+", rest)], ""
    refs = list(dict.fromkeys(refs))
    if len(refs) < 2:
        await bot.send(_MERGE_USAGE)
        return
    await bot.send(await prepare_task_merge(ev, cfg, refs, name, now))


async def _merge_draft(bot: Bot, ev: Event, t: str, rest: str, now: datetime) -> None:
    """草稿阶段合并：还没写进看板，直接改草稿。"""
    d = await _take_draft(bot, ev, t, "ddl合并{t}")
    if d is None:
        return
    if d.kind != "new":
        await bot.send(f"{d.label} 不是新建草稿，没什么可合并的。看板上已有的作业用 ddl合并#24#25")
        return
    if len(d.items) < 2:
        await bot.send(f"{d.label} 里只有一条，不用合并哦。")
        return
    nums = [int(x) for x in re.findall(r"\d+", rest)]
    picks = list(dict.fromkeys(nums)) or list(range(1, len(d.items) + 1))
    if len(picks) < 2 or any(not 1 <= i <= len(d.items) for i in picks):
        await bot.send(f"{d.label} 里有 {len(d.items)} 条，要写清楚合并哪几条，比如 ddl合并1 2；全部合并直接 ddl合并")
        return
    chosen = [d.items[i - 1] for i in picks]
    if any(it.target_id is not None for it in chosen):
        await bot.send(
            "这几条里有要更新看板上已有作业的，不能直接合并。先引用草稿回复 ddl第N条新建，或者确认后再用 ddl合并#编号"
        )
        return
    merged = merge_items(chosen)
    first = min(picks) - 1
    keep = [it for i, it in enumerate(d.items, 1) if i not in picks]
    d.items = keep[:first] + [merged] + keep[first:]
    d.created = time.time()
    await bot.send(render_draft(d, now, edited=True))


async def prepare_task_merge(ev: Event, cfg: Config, refs: list[int], name: str, now: datetime) -> str:
    """看板上的几条合成一条：先出合并草稿，确认后才写。并进第一个编号。"""
    api = _api(cfg)
    metas = load_meta()
    tasks: list[Json] = []
    for n in refs:
        task = await api.find_by_index(n)
        if task is None:
            return f"看板上没有 #{n} 这一条哦，发 ddl列表 看看编号？"
        tasks.append(task)
    items = [task_to_item(t, metas[str(jint(t, "id"))] if str(jint(t, "id")) in metas else None) for t in tasks]
    merged = merge_items(items, name)
    head = tasks[0]
    hid = jint(head, "id")
    merged.target_id, merged.target_index, merged.target_title = hid, refs[0], jstr(head, "title")
    d = Draft(
        kind="merge",
        owner=str(ev.user_id),
        owner_name=_sender_name(ev),
        created=time.time(),
        items=[merged],
        task_id=hid,
        task_index=refs[0],
        before=items[0],
        stamps={jint(t, "id"): _stamp(t) for t in tasks},
        merged=[(jint(t, "id"), n, jstr(t, "title")) for t, n in zip(tasks[1:], refs[1:])],
    )
    dropped = _add_draft(d)
    return _with_dropped(render_draft(d, now), dropped)


async def _cancel(bot: Bot, ev: Event, token: str) -> None:
    user = str(ev.user_id)
    if token in ("全部", "所有", "all"):
        mine = _my_drafts(user)
        for d in mine:
            del DRAFTS[d.tid]
        await bot.send(f"好吧，{len(mine)} 份草稿都扔掉了。" if mine else "你手上本来就没有草稿。")
        return
    d = await _take_draft(bot, ev, token, "ddl取消{t}")
    if d is None:
        return
    del DRAFTS[d.tid]
    await bot.send(f"好吧，{d.label} 就当没看见。")


async def _stale_tasks(api: Vikunja, d: Draft) -> list[str]:
    """起草之后又被改过（或删掉）的看板任务。"""
    out: list[str] = []
    for task_id, stamp in d.stamps.items():
        try:
            task = await api.get_task(task_id)
        except VikunjaError as e:
            if e.status == 404:
                out.append(f"有一条作业已经被删掉了（任务 {task_id}）")
                continue
            raise
        if _stamp(task) != stamp:
            out.append(f"#{jint(task, 'index')}「{short(jstr(task, 'title'), 20)}」")
    return out


async def _confirm(bot: Bot, ev: Event, cfg: Config, token: str, delete: bool) -> None:
    example = "ddl确认删除{t}" if delete else "ddl确认{t}"
    d = await _take_draft(bot, ev, token, example)
    if d is None:
        return
    if d.kind == "delete" and not delete:
        await bot.send(
            f"{d.label} 是删除草稿，要再确认一次才行：真的要删 #{d.task_index} 的话，引用草稿回复 ddl确认删除"
        )
        return
    if d.kind != "delete" and delete:
        await bot.send(f"{d.label} 不是删除草稿哦，写入的话引用草稿回复 ddl确认")
        return
    if d.kind == "edit" and d.before is not None and d.items[0] == d.before:
        del DRAFTS[d.tid]
        await bot.send(f"{d.label} 什么都没改呀，那就不动它了。")
        return
    api = _api(cfg)
    stale = await _stale_tasks(api, d)
    if stale:
        del DRAFTS[d.tid]
        await bot.send(
            "\n".join(
                [
                    f"{d.label} 先不写了：起草之后，看板上这些作业又被人改过：",
                    *[f"· {x}" for x in stale],
                    "",
                    "为了不把旧内容传上去，这份草稿已经作废。看一眼最新的（ddl 列表）再重新起草吧。",
                ]
            )
        )
        return
    del DRAFTS[d.tid]
    lines, error = await apply_draft(api, d, str(ev.user_id))
    if error is not None:
        logger.error(f"[IunoDDL] 写入出错：{error}")
        done = "\n".join(lines) if lines else "（一条都还没写进去）"
        await bot.send(
            f"{d.label} 写到一半出错了：{short(error, 100)}\n已经完成的：\n{done}"
            "\n可以发 ddl 撤销 把已完成的部分退回去。"
        )
        return
    head = f"{d.label} 删掉了。" if d.kind == "delete" else f"{d.label} {done_head()}"
    body = [f"· {x}" for x in lines]
    await bot.send("\n".join([head, "", *body, "", *board_line(cfg["board_url"]), "写错了的话：ddl 撤销"]))


async def _undo(bot: Bot, ev: Event, cfg: Config, ref: str) -> None:
    user = str(ev.user_id)
    recs = load_undo()
    rec: Optional[UndoRecord] = None
    if ref:
        kind, n = parse_ref(ref)
        if kind != "formal":
            await bot.send("撤销某一条要写 #编号，比如：ddl 撤销 #12；撤销自己上一次操作直接发 ddl 撤销")
            return
        for r in reversed(recs):
            if any(a["index"] == n for a in r["actions"]):
                rec = r
                break
        if rec is None:
            await bot.send(f"#{n} 最近 24 小时没有通过 QQ 的改动记录，没什么可撤销的哦。")
            return
        if rec["user"] != user and not _is_admin(ev, cfg):
            await bot.send(f"#{n} 最近一次是 {rec['user_name']} 改的，只有本人或管理员能撤销哦。")
            return
    else:
        mine = [r for r in recs if r["user"] == user]
        rec = mine[-1] if mine else None
    if rec is None:
        await bot.send("24 小时内没有可以撤销的操作哦。")
        return
    touched_later: dict[int, str] = {}
    for r in recs:
        if r["time"] > rec["time"]:
            for a in r["actions"]:
                touched_later[a["task_id"]] = r["user_name"]
    lines, error = await undo_record(_api(cfg), rec, f"{_sender_name(ev)}（QQ {user}）", touched_later)
    if error is not None:
        await bot.send(f"撤销时出错了：{short(error, 100)}\n" + "\n".join(lines))
        return
    skipped = [x for x in lines if "跳过" in x]
    head = "这次什么都没撤回：" if len(skipped) == len(lines) else "好啦，撤回去了。"
    await bot.send("\n".join([head, "", *[f"· {x}" for x in lines]]))
