"""把草稿写进看板、撤销、列表和详情。和触发器分开，方便单独测。"""

import re
import html
import time
import asyncio
from typing import Optional
from datetime import datetime

from ..utils.core import TZ, Draft, DDLItem, term, short, by_mode, fmt_due, board_line
from ..utils.store import (
    Snapshot,
    TaskMeta,
    UndoAction,
    UndoRecord,
    audit,
    load_meta,
    push_undo,
    save_meta,
    remove_undo,
)
from ..utils.vikunja import Json, Vikunja, VikunjaError, jint, jstr, task_due, to_vdate, parse_vdate, label_titles

apply_lock = asyncio.Lock()
_RE_TAG = re.compile(r"<[^>]+>")


def plain(description: str) -> str:
    text = re.sub(r"<br\s*/?>|</p>|</li>|</div>", "\n", description)
    return html.unescape(_RE_TAG.sub("", text)).strip()


def task_to_item(task: Json, meta: Optional[TaskMeta]) -> DDLItem:
    title = jstr(task, "title")
    labels = label_titles(task)
    due = task_due(task)
    item = DDLItem(course="", name=title, due=due, platform=labels[0] if labels else "", detail="")
    if meta is not None:
        item.course, item.name, item.detail = meta["course"], meta["name"], meta["detail"]
        if item.title != title:
            # 有人在网页上改过标题，以网页为准
            item.course, item.name = "", title
        if meta["platform"] in labels or not labels:
            item.platform = meta["platform"]
    item.target_id, item.target_index, item.target_title = jint(task, "id"), jint(task, "index"), title
    return item


def _sources_html(sources: list[str]) -> list[str]:
    e = html.escape
    parts: list[str] = []
    for i, src in enumerate(sources, 1):
        head = "原始通知" if len(sources) == 1 else f"原始通知 {i}"
        body = e(src[:4000]).replace("\n", "<br>")
        parts.append(f"<hr><p><strong>{head}</strong></p><blockquote><p>{body}</p></blockquote>")
    return parts


def render_desc(meta: TaskMeta) -> str:
    """生成任务描述。截止时间看板上单独显示，这里不写，免得改了截止以后描述里还是旧日期。"""
    e = html.escape
    if not meta["creator"]:
        # 插件接手之前就有的作业：原文一个字不动，QQ 补充的内容只附在后面
        parts = [meta["legacy"]]
        if meta["detail"]:
            parts.append(
                f"<hr><p><strong>QQ 补充的{term('detail')}</strong>：{e(meta['detail']).replace(chr(10), '<br>')}</p>"
            )
        return "".join(parts + _sources_html(meta["sources"]))
    parts = []
    if meta["course"]:
        parts.append(f"<p><strong>{term('course')}</strong>：{e(meta['course'])}</p>")
    parts.append(f"<p><strong>{term('item')}</strong>：{e(meta['name'])}</p>")
    if meta["platform"]:
        parts.append(f"<p><strong>{by_mode('办理方式', '提交平台')}</strong>：{e(meta['platform'])}</p>")
    if meta["detail"]:
        parts.append(f"<p><strong>{term('detail')}</strong>：{e(meta['detail']).replace(chr(10), '<br>')}</p>")
    if meta["legacy"]:
        parts.append("<hr><p><strong>原有说明</strong></p>" + meta["legacy"])
    parts += _sources_html(meta["sources"])
    parts.append(f"<p><em>由 {e(meta['creator'])} 通过 QQ 记录</em></p>")
    return "".join(parts)


def snapshot(task: Json, metas: dict[str, TaskMeta]) -> Snapshot:
    tid = jint(task, "id")
    return {
        "task_id": tid,
        "index": jint(task, "index"),
        "title": jstr(task, "title"),
        "description": jstr(task, "description"),
        "due": to_vdate(task_due(task)) or "",
        "labels": label_titles(task),
        "meta": metas[str(tid)] if str(tid) in metas else None,
    }


def find_match(item: DDLItem, tasks: list[Json]) -> Optional[Json]:
    title = item.title.strip()
    matches = [task for task in tasks if task["done"] is not True and jstr(task, "title").strip() == title]
    # 序号、教师或标点不同都可能是另一份作业；重名也不能替用户猜目标。
    return matches[0] if len(matches) == 1 else None


def known_courses(tasks: list[Json], metas: dict[str, TaskMeta]) -> list[str]:
    seen: list[str] = []
    for task in tasks:
        key = str(jint(task, "id"))
        if task["done"] is True or key not in metas:
            continue
        meta = metas[key]
        course = meta["course"]
        recorded = DDLItem(course=course, name=meta["name"], due=None, platform="", detail="")
        if recorded.title != jstr(task, "title"):
            continue
        if course and course not in seen:
            seen.append(course)
    return seen[:30]


def _comment(who: str, action: str, detail: str = "") -> str:
    tail = f"：{html.escape(detail)}" if detail else ""
    return f"<p>{html.escape(who)} 通过 QQ {action}{tail}</p>"


def _labels_after(labels: list[str], old_platform: str, new_platform: str) -> list[str]:
    out = [lb for lb in labels if lb != old_platform]
    if new_platform and new_platform not in out:
        out.insert(0, new_platform)
    return out


async def _write_update(
    api: Vikunja, metas: dict[str, TaskMeta], item: DDLItem, source: str, who: str, before: DDLItem
) -> tuple[Snapshot, Json]:
    assert item.target_id is not None
    task = await api.get_task(item.target_id)
    snap = snapshot(task, metas)
    key = str(item.target_id)
    if key in metas:
        meta = metas[key]
    else:
        meta = {
            "course": "",
            "name": "",
            "platform": "",
            "detail": "",
            "sources": [],
            "creator": "",
            "legacy": jstr(task, "description"),
        }
    new_source = bool(source) and source not in meta["sources"]
    if meta["creator"]:
        desc_changed = (before.course, before.name, before.platform, before.detail) != (
            item.course,
            item.name,
            item.platform,
            item.detail,
        )
    else:
        # 老作业的描述里只有原文和 QQ 补充的要求，只有要求变了才需要重写
        desc_changed = before.detail != item.detail
    meta["course"], meta["name"], meta["platform"], meta["detail"] = item.course, item.name, item.platform, item.detail
    if new_source:
        meta["sources"].append(source)
    due = item.due if item.due is not None else task_due(task)
    # 只改截止、标题这类字段时，描述原样保留，不重新生成
    desc = render_desc(meta) if desc_changed or new_source else jstr(task, "description")
    updated = await api.update_task(item.target_id, item.title, desc, due)
    labels = label_titles(task)
    new_labels = _labels_after(labels, before.platform, item.platform)
    if new_labels != labels:
        await api.set_labels(item.target_id, new_labels)
    metas[key] = meta
    return snap, updated


def _change_summary(before: DDLItem, after: DDLItem) -> str:
    now = datetime.now(TZ)
    out: list[str] = []
    if before.title != after.title:
        out.append(f"标题 {before.title} → {after.title}")
    if before.due != after.due:
        out.append(f"截止 {fmt_due(before.due, now, False)} → {fmt_due(after.due, now, False)}")
    if before.platform != after.platform:
        out.append(f"{term('platform')} {before.platform or '无'} → {after.platform or '无'}")
    if before.detail != after.detail:
        out.append(f"{term('detail')}有改动")
    return "；".join(out) or "无字段变化（补充了通知原文）"


async def apply_draft(api: Vikunja, d: Draft, user: str) -> tuple[list[str], Optional[str]]:
    """执行草稿。返回（结果行，出错信息）。出错时已经完成的部分照样可以撤销。"""
    who = f"{d.owner_name}（QQ {user}）"
    lines: list[str] = []
    actions: list[UndoAction] = []
    error: Optional[str] = None
    async with apply_lock:
        metas = load_meta()
        try:
            if d.kind == "delete":
                assert d.task_id is not None
                task = await api.get_task(d.task_id)
                snap = snapshot(task, metas)
                await api.delete_task(d.task_id)
                metas.pop(str(d.task_id), None)
                actions.append({"kind": "deleted", "task_id": d.task_id, "index": snap["index"], "snapshot": snap})
                lines.append(f"删掉了 #{snap['index']} {snap['title']}")
            elif d.kind == "merge":
                lines += await _apply_merge(api, metas, d, who, actions)
            elif d.kind == "edit":
                assert d.before is not None
                it = d.items[0]
                snap, updated = await _write_update(api, metas, it, "", who, d.before)
                summary = _change_summary(d.before, it)
                await api.add_comment(it.target_id or 0, _comment(who, "修改", summary))
                actions.append(
                    {"kind": "updated", "task_id": snap["task_id"], "index": snap["index"], "snapshot": snap}
                )
                lines.append(f"改好了 #{jint(updated, 'index')} {it.title}：{summary}")
            else:
                now = datetime.now(TZ)
                for it in d.items:
                    if it.target_id is not None:
                        old = task_to_item(await api.get_task(it.target_id), metas.get(str(it.target_id)))
                        snap, updated = await _write_update(api, metas, it, d.source, who, old)
                        summary = _change_summary(old, it)
                        await api.add_comment(it.target_id, _comment(who, "转发更新", summary))
                        actions.append(
                            {"kind": "updated", "task_id": snap["task_id"], "index": snap["index"], "snapshot": snap}
                        )
                        lines.append(f"更新 #{jint(updated, 'index')} {it.title}（{fmt_due(it.due, now, False)}）")
                        continue
                    meta: TaskMeta = {
                        "course": it.course,
                        "name": it.name,
                        "platform": it.platform,
                        "detail": it.detail,
                        "sources": [d.source] if d.source else [],
                        "creator": who,
                        "legacy": "",
                    }
                    created = await api.create_task(it.title, render_desc(meta), it.due)
                    tid, idx = jint(created, "id"), jint(created, "index")
                    metas[str(tid)] = meta
                    actions.append({"kind": "created", "task_id": tid, "index": idx, "snapshot": None})
                    if it.platform:
                        await api.set_labels(tid, [it.platform])
                    await api.add_comment(tid, _comment(who, "新建"))
                    lines.append(f"新增 #{idx} {it.title}（{fmt_due(it.due, now, False)}）")
        except VikunjaError as e:
            error = str(e)
        finally:
            await save_meta(metas)
            if actions:
                rec: UndoRecord = {
                    "user": user,
                    "user_name": d.owner_name,
                    "time": time.time(),
                    "summary": "；".join(lines),
                    "actions": actions,
                }
                await push_undo(rec)
                await audit({"op": d.kind, "user": user, "name": d.owner_name, "result": lines, "error": error})
    return lines, error


def _legacy_of(meta: Optional[TaskMeta], task: Json) -> str:
    """插件接手之前就有的原文：没有记录的取当前描述，有记录的取当时存下的原文。"""
    return jstr(task, "description") if meta is None else meta["legacy"]


async def _apply_merge(
    api: Vikunja, metas: dict[str, TaskMeta], d: Draft, who: str, actions: list[UndoAction]
) -> list[str]:
    """合并：第一条改成合并后的内容（几条的原始通知都保留），其余几条删掉。每一步都进撤销记录。"""
    it = d.items[0]
    assert it.target_id is not None
    task = await api.get_task(it.target_id)
    snap = snapshot(task, metas)
    key = str(it.target_id)
    base = metas[key] if key in metas else None
    sources: list[str] = list(base["sources"]) if base is not None else []
    legacy = [_legacy_of(base, task)]
    for oid, _, _ in d.merged:
        om = metas[str(oid)] if str(oid) in metas else None
        legacy.append(_legacy_of(om, await api.get_task(oid)))
        for src in om["sources"] if om is not None else []:
            if src not in sources:
                sources.append(src)
    meta: TaskMeta = {
        "course": it.course,
        "name": it.name,
        "platform": it.platform,
        "detail": it.detail,
        "sources": sources,
        "creator": base["creator"] if base is not None and base["creator"] else who,
        # 插件接手之前的老作业，原有描述一个字不丢，附在后面
        "legacy": "<hr>".join(x for x in legacy if x),
    }
    updated = await api.update_task(it.target_id, it.title, render_desc(meta), it.due)
    labels = label_titles(task)
    for p in [x for x in it.platform.split("、") if x]:
        if p not in labels:
            labels.append(p)
    if labels != label_titles(task):
        await api.set_labels(it.target_id, labels)
    metas[key] = meta
    others = "、".join(f"#{idx}" for _, idx, _ in d.merged)
    await api.add_comment(it.target_id, _comment(who, "合并", f"并入 {others}"))
    actions.append({"kind": "updated", "task_id": snap["task_id"], "index": snap["index"], "snapshot": snap})
    out = [f"#{jint(updated, 'index')} 合并成了「{it.title}」（{fmt_due(it.due, datetime.now(TZ), False)}）"]
    # 倒着删：撤销时按记录倒序重建，正好恢复成原来的先后顺序
    for oid, idx, _ in reversed(d.merged):
        other = await api.get_task(oid)
        osnap = snapshot(other, metas)
        await api.delete_task(oid)
        metas.pop(str(oid), None)
        actions.append({"kind": "deleted", "task_id": oid, "index": osnap["index"], "snapshot": osnap})
        out.append(f"删掉了并进去的 #{idx} {osnap['title']}")
    return out[:1] + out[:0:-1]


async def _restore(api: Vikunja, metas: dict[str, TaskMeta], snap: Snapshot, task_id: int) -> None:
    due = parse_vdate(snap["due"])
    await api.update_task(task_id, snap["title"], snap["description"], due)
    await api.set_labels(task_id, snap["labels"])
    if snap["meta"] is not None:
        metas[str(task_id)] = snap["meta"]
    else:
        metas.pop(str(task_id), None)


async def _exists(api: Vikunja, task_id: int) -> bool:
    try:
        await api.get_task(task_id)
    except VikunjaError as e:
        if e.status == 404:
            return False
        raise
    return True


async def undo_record(
    api: Vikunja, rec: UndoRecord, who: str, touched_later: dict[int, str]
) -> tuple[list[str], Optional[str]]:
    """touched_later：这次操作之后又被别人改过的任务 id → 改的人。这些任务跳过，免得冲掉别人的修改。"""
    lines: list[str] = []
    error: Optional[str] = None
    async with apply_lock:
        metas = load_meta()
        try:
            for act in reversed(rec["actions"]):
                snap = act["snapshot"]
                if act["kind"] in ("created", "updated") and act["task_id"] in touched_later:
                    lines.append(
                        f"#{act['index']} 之后又被 {touched_later[act['task_id']]} 改过，为了不冲掉别人的修改先跳过了"
                        f"（需要的话直接 ddl 改 #{act['index']}）"
                    )
                elif act["kind"] in ("created", "updated") and not await _exists(api, act["task_id"]):
                    metas.pop(str(act["task_id"]), None)
                    lines.append(f"#{act['index']} 已经被删掉了，跳过")
                elif act["kind"] == "created":
                    await api.delete_task(act["task_id"])
                    metas.pop(str(act["task_id"]), None)
                    lines.append(f"删掉了刚才新增的 #{act['index']}")
                elif act["kind"] == "updated" and snap is not None:
                    await _restore(api, metas, snap, act["task_id"])
                    await api.add_comment(act["task_id"], _comment(who, "撤销了上一次修改"))
                    lines.append(f"#{act['index']} 恢复成修改前的样子了")
                elif act["kind"] == "deleted" and snap is not None:
                    created = await api.create_task(snap["title"], snap["description"], parse_vdate(snap["due"]))
                    tid = jint(created, "id")
                    if snap["labels"]:
                        await api.set_labels(tid, snap["labels"])
                    if snap["meta"] is not None:
                        metas[str(tid)] = snap["meta"]
                    await api.add_comment(tid, _comment(who, f"撤销删除，从 #{snap['index']} 恢复"))
                    lines.append(f"#{snap['index']} 恢复回来了，新编号是 #{jint(created, 'index')}")
        except VikunjaError as e:
            error = str(e)
        finally:
            await save_meta(metas)
            if error is None:
                await remove_undo(rec)
            await audit({"op": "undo", "by": who, "record": rec["summary"], "result": lines, "error": error})
    return lines, error


def _list_entry(t: Json, due: Optional[datetime], now: datetime) -> list[str]:
    lines = [f"#{jint(t, 'index')} {short(jstr(t, 'title'), 40)}"]
    if due is not None:
        lines.append(f"　截止：{fmt_due(due, now)}")
    labels = label_titles(t)
    if labels:
        lines.append(f"　{term('platform')}：{'、'.join(labels)}")
    return lines


def render_list(tasks: list[Json], board_url: str) -> str:
    now = datetime.now(TZ)
    open_tasks = [t for t in tasks if t["done"] is not True]
    if not open_tasks:
        return "\n\n".join([f"看板上现在一条{term('item')}都没有……难得清闲嘛。", *board_line(board_url)])
    with_due = [(task_due(t), t) for t in open_tasks]
    dated = sorted([(d, t) for d, t in with_due if d is not None], key=lambda x: x[0])
    groups: list[tuple[str, list[tuple[Optional[datetime], Json]]]] = [
        ("⚠️ 已经过了截止", [(d, t) for d, t in dated if d < now]),
        (
            by_mode("🔥 3 天内到期", "🔥 3 天内要交"),
            [(d, t) for d, t in dated if now <= d and (d.date() - now.date()).days <= 3],
        ),
        (by_mode("📅 之后到期", "📅 之后要交"), [(d, t) for d, t in dated if (d.date() - now.date()).days > 3]),
        ("❔ 还没写截止时间", [(d, t) for d, t in with_due if d is None]),
    ]
    lines = [f"没完成的{term('item')}一共 {len(open_tasks)} 条："]
    shown = 0
    for head, items in groups:
        if not items:
            continue
        lines += ["", f"【{head}】"]
        for i, (d, t) in enumerate(items):
            if shown >= 30:
                break
            if i:
                lines.append("")
            lines += _list_entry(t, d, now)
            shown += 1
    if shown < len(open_tasks):
        lines += ["", f"……还有 {len(open_tasks) - shown} 条，去看板上看吧"]
    lines += ["", "看详情或修改：ddl#编号", *board_line(board_url)]
    return "\n".join(lines)


def render_detail(task: Json, meta: Optional[TaskMeta], board_url: str) -> str:
    now = datetime.now(TZ)
    it = task_to_item(task, meta)
    # 「作业 #n」「事项 #n」是引用这张卡片修改时用来认编号的标记，别改掉
    lines = [f"📌 {term('item')} #{jint(task, 'index')}：{jstr(task, 'title')}", "", f"截止：{fmt_due(it.due, now)}"]
    if it.platform:
        lines.append(f"{term('platform')}：{it.platform}")
    description = jstr(task, "description")
    if it.detail:
        detail = it.detail
    elif meta is None:
        # 插件没记过的条目（比如在网页上直接建的）：网页上的描述就是说明
        detail = plain(description)
    elif not meta["creator"]:
        # 插件接手之前就有的老条目：显示它原来的描述
        detail = plain(meta["legacy"])
    elif description != render_desc(meta):
        # 有人在网页上改过描述，别把改的东西藏起来
        detail = plain(description)
    else:
        # 插件自己生成的描述里只有名称、原文和记录人，卡片上已经都有了，不再重复
        detail = ""
    if detail:
        lines.append(f"{term('detail')}：{short(detail, 150)}")
    if meta is not None and meta["creator"]:
        lines.append(f"记录人：{meta['creator']}")
    if task["done"] is True:
        lines.append("（这条在看板上已经标成完成了）")
    lines += [
        "",
        "要改的话，引用这条消息回复：",
        "· ddl 截止周五晚上 → 改截止",
        by_mode("· ddl 记得带邀请函 → 改说明，字段名不写也行", "· ddl 要交PDF和源码 → 改要求，字段名不写也行"),
        "· ddl删 → 删除这条",
        "改完会先给你看对比，确认了才写进看板。",
        *board_line(board_url),
    ]
    return "\n".join(lines)
