"""配置与本地状态：data/IunoDDL/ 下的几个 JSON 文件。"""

import json
import time
import asyncio
from typing import TypedDict
from pathlib import Path

from gsuid_core.data_store import get_res_path

from .core import set_bot_name

MAIN_PATH = get_res_path() / "IunoDDL"
CONFIG_PATH = MAIN_PATH / "config.json"
META_PATH = MAIN_PATH / "task_meta.json"
UNDO_PATH = MAIN_PATH / "undo.json"
AUDIT_PATH = MAIN_PATH / "audit.jsonl"
UNDO_TTL = 86400
UNDO_KEEP = 5

MAIN_PATH.mkdir(parents=True, exist_ok=True)
MAIN_PATH.chmod(0o700)


class Config(TypedDict):
    bot_name: str
    vikunja_api: str
    vikunja_token: str
    project_id: int
    view_id: int
    board_url: str
    allowed_groups: list[str]
    admins: list[str]
    private_users: list[str]


DEFAULT_CONFIG: Config = {
    "bot_name": "尤诺",
    "vikunja_api": "http://127.0.0.1:3456/api/v1",
    "vikunja_token": "",
    "project_id": 0,
    "view_id": 0,
    "board_url": "",
    "allowed_groups": [],
    "admins": [],
    "private_users": [],
}

# 第一次生成 config.json 时一起写进去的说明，插件不读这一项
CONFIG_HELP = {
    "bot_name": "机器人在回复和帮助里的自称，默认「尤诺」；改了以后重启 GSCore，帮助图才会重画",
    "vikunja_api": "Vikunja 的 API 地址，以 /api/v1 结尾；和 GSCore 在同一台机器上就用 127.0.0.1",
    "vikunja_token": "Vikunja 的 API 令牌，要有任务、标签、评论的读写权限；deploy-site.sh 会自动生成",
    "project_id": "看板项目的编号，也就是项目网址 /projects/<编号> 里的数字",
    "view_id": "这个项目「列表」视图的编号，也就是网址 /projects/<项目>/<视图> 里的第二个数字",
    "board_url": "公开看板的网址，附在回复末尾；留空就不附",
    "allowed_groups": "开放 DDL 功能的 QQ 群号，写成字符串；默认一个群都不开",
    "admins": "能撤销别人的操作、也能私聊使用的 QQ 号；GSCore 的主人和超级管理员本来就算",
    "private_users": "另外允许私聊使用的 QQ 号",
}


class TaskMeta(TypedDict):
    course: str
    name: str
    platform: str
    detail: str
    sources: list[str]
    creator: str
    # 插件接手之前任务原有的描述，重新生成描述时附在后面，不丢
    legacy: str


class Snapshot(TypedDict):
    task_id: int
    index: int
    title: str
    description: str
    due: str
    labels: list[str]
    meta: TaskMeta | None


class UndoAction(TypedDict):
    kind: str  # created / updated / deleted
    task_id: int
    index: int
    snapshot: Snapshot | None


class UndoRecord(TypedDict):
    user: str
    user_name: str
    time: float
    summary: str
    actions: list[UndoAction]


_lock = asyncio.Lock()


def _write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)


def load_config() -> Config:
    if not CONFIG_PATH.exists():
        _write(CONFIG_PATH, json.dumps({"_说明": CONFIG_HELP, **DEFAULT_CONFIG}, ensure_ascii=False, indent=2))
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    raw.pop("_说明", None)
    cfg: Config = {**DEFAULT_CONFIG, **raw}
    cfg["bot_name"] = str(cfg["bot_name"]).strip() or DEFAULT_CONFIG["bot_name"]
    cfg["project_id"] = int(cfg["project_id"])
    cfg["view_id"] = int(cfg["view_id"])
    cfg["allowed_groups"] = [str(x) for x in cfg["allowed_groups"]]
    cfg["admins"] = [str(x) for x in cfg["admins"]]
    cfg["private_users"] = [str(x) for x in cfg["private_users"]]
    set_bot_name(cfg["bot_name"])
    return cfg


def is_configured(cfg: Config) -> bool:
    """令牌、项目、视图都填好了才能访问看板。"""
    return bool(cfg["vikunja_token"]) and cfg["project_id"] > 0 and cfg["view_id"] > 0


def _load_json(path: Path, default: str) -> object:
    if not path.exists():
        return json.loads(default)
    return json.loads(path.read_text(encoding="utf-8"))


def load_meta() -> dict[str, TaskMeta]:
    data = _load_json(META_PATH, "{}")
    return data if isinstance(data, dict) else {}


async def save_meta(meta: dict[str, TaskMeta]) -> None:
    async with _lock:
        await asyncio.to_thread(_write, META_PATH, json.dumps(meta, ensure_ascii=False, indent=1))


def load_undo() -> list[UndoRecord]:
    data = _load_json(UNDO_PATH, "[]")
    return data if isinstance(data, list) else []


async def push_undo(rec: UndoRecord) -> None:
    async with _lock:
        now = time.time()
        recs = [r for r in load_undo() if now - r["time"] < UNDO_TTL]
        recs.append(rec)
        # 每人最多留几条
        mine = [r for r in recs if r["user"] == rec["user"]]
        if len(mine) > UNDO_KEEP:
            drop = mine[0]
            recs = [r for r in recs if r is not drop]
        await asyncio.to_thread(_write, UNDO_PATH, json.dumps(recs, ensure_ascii=False, indent=1))


async def remove_undo(rec: UndoRecord) -> None:
    async with _lock:
        recs = [r for r in load_undo() if not (r["user"] == rec["user"] and r["time"] == rec["time"])]
        await asyncio.to_thread(_write, UNDO_PATH, json.dumps(recs, ensure_ascii=False, indent=1))


def _append(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    path.chmod(0o600)


async def audit(entry: dict[str, object]) -> None:
    entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), **entry}
    await asyncio.to_thread(_append, AUDIT_PATH, json.dumps(entry, ensure_ascii=False))
