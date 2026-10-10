"""Vikunja API 的最小封装：只用到 ddl 看板需要的几个接口。"""

import hashlib
from typing import Optional
from datetime import datetime

import httpx

from .core import TZ

Json = dict[str, object]
_PALETTE = ("1973ff", "e8445a", "ff9f1a", "10b981", "8b5cf6", "0ea5e9", "f43f5e", "64748b")


class VikunjaError(Exception):
    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


def jstr(d: Json, key: str) -> str:
    v = d[key] if key in d else None
    return v if isinstance(v, str) else ""


def jint(d: Json, key: str) -> int:
    v = d[key] if key in d else None
    return v if isinstance(v, int) else 0


def jlist(d: Json, key: str) -> list[Json]:
    v = d[key] if key in d else None
    if not isinstance(v, list):
        return []
    return [x for x in v if isinstance(x, dict)]


def parse_vdate(value: str) -> Optional[datetime]:
    if not value or value.startswith(("0001-", "0000-")):
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TZ)


def task_due(task: dict[str, object]) -> Optional[datetime]:
    """和看板网页同一个规则：先看截止日期 due_date，没有就用结束日期 end_date。"""
    due = parse_vdate(jstr(task, "due_date"))
    return due if due is not None else parse_vdate(jstr(task, "end_date"))


def to_vdate(value: Optional[datetime]) -> Optional[str]:
    return value.astimezone(TZ).isoformat() if value is not None else None


def label_titles(task: Json) -> list[str]:
    return [jstr(lb, "title") for lb in jlist(task, "labels") if jstr(lb, "title")]


def _valid_task(task: Json) -> bool:
    if any(key not in task for key in ("id", "index", "title", "done")):
        return False
    return (
        type(task["id"]) is int
        and jint(task, "id") > 0
        and type(task["index"]) is int
        and jint(task, "index") > 0
        and isinstance(task["title"], str)
        and bool(jstr(task, "title").strip())
        and type(task["done"]) is bool
    )


class Vikunja:
    def __init__(self, base: str, token: str, project_id: int, view_id: int) -> None:
        self.base = base.rstrip("/")
        self.token = token
        self.project_id = project_id
        self.view_id = view_id

    async def _req(self, method: str, path: str, body: Optional[object] = None) -> object:
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            resp = await client.request(
                method,
                f"{self.base}{path}",
                json=body,
                headers={"Authorization": f"Bearer {self.token}"},
            )
        if resp.status_code >= 400:
            raise VikunjaError(f"{method} {path} -> {resp.status_code} {resp.text[:200]}", resp.status_code)
        return resp.json() if resp.content else None

    async def _obj(self, method: str, path: str, body: Optional[object] = None) -> Json:
        data = await self._req(method, path, body)
        if not isinstance(data, dict):
            raise VikunjaError(f"{method} {path} 返回的不是对象")
        return data

    async def list_tasks(self) -> list[Json]:
        """项目里的全部任务（含已完成），按页取完。"""
        out: list[Json] = []
        page = 1
        while True:
            data = await self._req(
                "GET", f"/projects/{self.project_id}/views/{self.view_id}/tasks?per_page=50&page={page}"
            )
            if not isinstance(data, list) or any(not isinstance(task, dict) for task in data):
                raise VikunjaError("看板任务列表格式异常")
            if not data:
                break
            page_tasks: list[Json] = [task for task in data if isinstance(task, dict)]
            if not all(_valid_task(task) for task in page_tasks):
                raise VikunjaError("看板任务的编号或内容格式异常")
            out.extend(page_tasks)
            if len(data) < 50:
                break
            page += 1
        return out

    async def find_by_index(self, index: int) -> Optional[Json]:
        for t in await self.list_tasks():
            if jint(t, "index") == index:
                return await self.get_task(jint(t, "id"))
        return None

    async def get_task(self, task_id: int) -> Json:
        task = await self._obj("GET", f"/tasks/{task_id}")
        if not _valid_task(task):
            raise VikunjaError("看板任务详情格式异常")
        return task

    async def create_task(self, title: str, description: str, due: Optional[datetime]) -> Json:
        body = {"title": title, "description": description, "due_date": to_vdate(due)}
        return await self._obj("PUT", f"/projects/{self.project_id}/tasks", body)

    async def update_task(self, task_id: int, title: str, description: str, due: Optional[datetime]) -> Json:
        # Vikunja 的更新接口会把没传的字段清空，所以先取完整任务再改
        task = await self.get_task(task_id)
        task["title"] = title
        task["description"] = description
        task["due_date"] = to_vdate(due)
        return await self._obj("POST", f"/tasks/{task_id}", task)

    async def delete_task(self, task_id: int) -> None:
        await self._req("DELETE", f"/tasks/{task_id}")

    async def ensure_label(self, title: str) -> int:
        data = await self._req("GET", "/labels?per_page=200")
        if isinstance(data, list):
            for lb in data:
                if isinstance(lb, dict) and jstr(lb, "title") == title:
                    return jint(lb, "id")
        color = _PALETTE[int(hashlib.md5(title.encode()).hexdigest(), 16) % len(_PALETTE)]
        created = await self._obj("PUT", "/labels", {"title": title, "hex_color": color})
        return jint(created, "id")

    async def set_labels(self, task_id: int, titles: list[str]) -> None:
        ids = [await self.ensure_label(t) for t in titles if t]
        await self._req("POST", f"/tasks/{task_id}/labels/bulk", {"labels": [{"id": i} for i in ids]})

    async def add_comment(self, task_id: int, html: str) -> None:
        await self._req("PUT", f"/tasks/{task_id}/comments", {"comment": html})
