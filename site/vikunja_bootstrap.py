#!/usr/bin/env python3
"""初始化 Vikunja：登录管理员，建看板项目，开只读公开分享，生成插件用的 API 令牌。

只用标准库，deploy-site.sh 会调用它，也可以单独运行。重复运行是安全的：同名项目、同名分享会沿用；
输出文件里的令牌还能用就不再新建。管理员密码从环境变量 VIKUNJA_ADMIN_PASSWORD 读，不走命令行参数。

输出（权限 600）：{"project_id", "view_id", "share_hash", "token"}
"""

import os
import sys
import json
import argparse
import urllib.error
import urllib.request
from pathlib import Path

SHARE_NAME = "公开看板"
TOKEN_TITLE = "IunoDDL 插件"
# 插件实际用到的接口，多一项都不给
TOKEN_PERMISSIONS = {
    "projects_views_tasks": ["read_all"],
    "tasks": ["read_one", "create", "update", "delete"],
    "labels": ["read_all", "create"],
    "tasks_labels": ["update_bulk"],
    "tasks_comments": ["create"],
}
# Vikunja 的令牌必须有过期时间，网页上的「永不过期」也是这个值
NEVER = "9999-12-31T23:59:59Z"
# 分享：permission 0 是只读，sharing_type 1 是不要密码
READ_ONLY, NO_PASSWORD = 0, 1


class ApiError(Exception):
    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.token = ""
        # 访问的是本机端口，不走系统代理
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, method: str, path: str, body: object = None, token: str | None = None) -> object:
        headers = {"Content-Type": "application/json"}
        auth = self.token if token is None else token
        if auth:
            headers["Authorization"] = f"Bearer {auth}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=20) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")[:200]
            raise ApiError(f"{method} {path} -> {e.code} {text}", e.code) from None
        return json.loads(raw) if raw else None


def _token_works(api: Api, token: str, project_id: int, view_id: int) -> bool:
    try:
        api.call("GET", f"/projects/{project_id}/views/{view_id}/tasks?per_page=1", token=token)
    except ApiError as e:
        if e.status in (401, 403, 404):
            return False
        raise
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--api", required=True, help="Vikunja 的 API 地址，例如 http://127.0.0.1:3456/api/v1")
    ap.add_argument("--username", required=True, help="Vikunja 管理员用户名")
    ap.add_argument("--project", default="DDL", help="看板项目的名字，默认 DDL")
    ap.add_argument("--out", required=True, type=Path, help="结果写到哪个文件")
    args = ap.parse_args()
    password = os.environ.get("VIKUNJA_ADMIN_PASSWORD", "")
    if not password:
        sys.exit("需要环境变量 VIKUNJA_ADMIN_PASSWORD")

    api = Api(args.api)
    login = api.call("POST", "/login", {"username": args.username, "password": password})
    assert isinstance(login, dict)
    api.token = login["token"]

    projects = api.call("GET", "/projects?per_page=50")
    assert isinstance(projects, list)
    project = next((p for p in projects if p["title"] == args.project and not p.get("is_archived")), None)
    if project is None:
        project = api.call("PUT", "/projects", {"title": args.project})
        print(f"新建项目「{args.project}」")
    assert isinstance(project, dict)
    project_id = project["id"]

    views = api.call("GET", f"/projects/{project_id}/views")
    assert isinstance(views, list)
    view = next((v for v in views if v["view_kind"] == "list"), None)
    if view is None:
        sys.exit(f"项目 {project_id} 没有「列表」视图，请在网页上给它加一个再重试")
    view_id = view["id"]

    shares = api.call("GET", f"/projects/{project_id}/shares")
    assert isinstance(shares, list)
    share = next(
        (
            s
            for s in shares
            if s.get("name") == SHARE_NAME and s.get("permission") == READ_ONLY and s.get("sharing_type") == NO_PASSWORD
        ),
        None,
    )
    if share is None:
        share = api.call(
            "PUT",
            f"/projects/{project_id}/shares",
            {"name": SHARE_NAME, "permission": READ_ONLY, "sharing_type": NO_PASSWORD},
        )
        print("新建只读公开分享")
    assert isinstance(share, dict)
    share_hash = share["hash"]

    old = json.loads(args.out.read_text(encoding="utf-8")) if args.out.exists() else {}
    token = old.get("token", "") if old.get("project_id") == project_id else ""
    if not token or not _token_works(api, token, project_id, view_id):
        created = api.call(
            "PUT", "/tokens", {"title": TOKEN_TITLE, "expires_at": NEVER, "permissions": TOKEN_PERMISSIONS}
        )
        assert isinstance(created, dict)
        token = created["token"]
        print("新建插件用的 API 令牌")

    # 按看板前端的走法检查一遍：用分享换临时令牌，再读列表视图
    auth = api.call("POST", f"/shares/{share_hash}/auth", {"password": ""}, token="")
    assert isinstance(auth, dict)
    api.call("GET", f"/projects/{project_id}/views/{view_id}/tasks?per_page=1", token=auth["token"])
    if not _token_works(api, token, project_id, view_id):
        sys.exit("新令牌读不了看板，请检查 Vikunja 版本")

    result = {"project_id": project_id, "view_id": view_id, "share_hash": share_hash, "token": token}
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    os.chmod(args.out, 0o600)
    print(f"项目 {project_id}，列表视图 {view_id}，结果已写入 {args.out}")


if __name__ == "__main__":
    try:
        main()
    except (ApiError, urllib.error.URLError) as e:
        sys.exit(f"访问 Vikunja 出错：{e}")
