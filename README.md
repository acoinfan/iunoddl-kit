# iunoddl-kit

尤诺的 DDL 小本本：GSCore 插件 IunoDDL + 公开只读的 DDL 看板（Vikunja）。

群友在 QQ 里转发作业通知、发 `ddl` 或者 @机器人，插件把通知整理成草稿，本人确认后写进 Vikunja；看板网页公开只读，展示还没完成的作业和时间轴。机器人默认自称「尤诺」，可以改。

QQ 机器人本身（NapCat 登录 QQ、NoneBot2、GSCore）怎么搭这里不讲，默认你已经有一个能正常回消息的 GSCore。

## 目录

| 路径 | 内容 |
| --- | --- |
| `plugin/IunoDDL/` | GSCore 插件，用法和配置见 [plugin/IunoDDL/README.md](plugin/IunoDDL/README.md) |
| `install-plugin.sh` | 把插件装进 GSCore 并写好配置 |
| `deploy-site.sh` | 一键部署看板：Vikunja、项目、只读分享、插件令牌、网页、nginx、证书 |
| `site/compose.yaml` | Vikunja 2.6.0 的 docker compose，域名、端口、密钥从同目录的 `.env` 读 |
| `site/vikunja_bootstrap.py` | 初始化 Vikunja：建项目、开只读分享、生成最小权限的插件令牌 |
| `site/index.html` | 看板前端模板 |
| `site/nginx/` | nginx 模板：`iunoddl-http.conf`（80 端口，申请证书用）和 `iunoddl-https.conf`（看板和后台） |

## 我们自己在用的版本

照着这些版本搭，最不容易出问题。

| 组件 | 版本 | 备注 |
| --- | --- | --- |
| 系统 | Ubuntu 24.04 LTS | 22.04 应该也行，没专门测 |
| Docker / Compose | 29.1.3 / 2.40.3 | Ubuntu 软件源里的 `docker.io` 和 `docker-compose-v2` |
| nginx | 1.24.0 | |
| certbot | 2.9.0 | |
| Vikunja | 2.6.0 | 镜像 `vikunja/vikunja:2.6.0`，SQLite |
| GSCore（gsuid_core） | 0.10.7 | Python 3.12.3。插件用到 GSCore 的 `ai_core` 和它环境里的 pydantic_ai、httpx、Pillow；记新作业时要调用模型解析通知，所以 GSCore 里要配好 AI 模型（查看、确认、撤销这些不用模型） |
| NoneBot2 | 2.5.0 | 配 nonebot-adapter-onebot 2.4.6 和 nonebot-plugin-genshinuid 6.1.1，把 QQ 消息转给 GSCore |
| NapCat | 4.18.28 | 自己打的 docker 镜像，内置 LinuxQQ 3.2.22-42941，用 OneBot v11 的 WebSocket 连 NoneBot2 |

插件没有额外的 Python 依赖，用 GSCore 环境里已有的就够。

## 方案 B：一键部署

准备：

- 一台 Ubuntu 服务器，有 root；
- 两个域名都解析到这台服务器，一个给看板（例如 `ddl.example.com`），一个给 Vikunja 管理后台（例如 `todo.example.com`）；
- 80 和 443 端口能从外网访问；
- 装好依赖：`sudo apt install docker.io docker-compose-v2 nginx certbot python3 openssl curl`。

然后在仓库目录里运行：

```bash
sudo ./deploy-site.sh --ddl ddl.example.com --todo todo.example.com --email you@example.com
sudo ./install-plugin.sh --gscore /path/to/gsuid_core \
    --config /opt/iunoddl-vikunja/iunoddl-config.json --groups 群号1,群号2
sudo systemctl restart gscore   # 按你自己启动 GSCore 的方式重启
```

`deploy-site.sh` 依次做这些事，哪一步出错就停在哪一步，修好后可以直接重跑：

1. 检查参数、依赖、端口，确认 nginx 里没有别的站点占用这两个域名，现有配置能通过 `nginx -t`；
2. 在 `/opt/iunoddl-vikunja` 起 Vikunja（只监听 `127.0.0.1:3456`），随机生成签名密钥写进 `.env`；
3. 用 Vikunja 的命令行建管理员 `ddladmin`，随机密码存在 `/opt/iunoddl-vikunja/admin-password`；
4. 建「DDL」项目，开一个只读、不要密码的公开分享，生成插件用的 API 令牌（只给插件用得到的权限，永不过期）；
5. 生成看板网页，放到 `/srv/www/<看板域名>`；
6. 写 nginx 的 80 端口配置，用 certbot 给两个域名申请一张证书（续期后自动重载 nginx），再写 443 端口配置。每次写完都先 `nginx -t`，检查不过就撤回，nginx 保持原样；重载后会等新配置真正生效（nginx 的重载是异步的）再往下走；
7. 生成插件配置 `/opt/iunoddl-vikunja/iunoddl-config.json`，最后从本机访问一遍看板、接口和后台。

重跑时已有的密钥、管理员、项目、分享和令牌都会沿用。常用选项（完整列表见 `./deploy-site.sh --help`）：

- `--frame-ancestors "'self' https://blog.example.com"`：允许别的网站用 iframe 嵌入看板。看板地址后面加 `?embed=1` 是去掉页头的嵌入版；
- `--skip-certbot --cert-dir 目录`：已经有证书时不再申请；
- `--port`、`--dir`、`--web-root`、`--admin`、`--project`：换端口、安装目录、网页目录、管理员用户名、项目名。

`install-plugin.sh` 把插件复制到 `gsuid_core/plugins/IunoDDL`（已有的旧版本挪到 `.iunoddl-backups/`），再把 `--config` 里的看板地址和令牌、`--groups` 等参数合并进 `data/IunoDDL/config.json`。已有的配置会保留，只覆盖这次给出的字段，所以升级插件也用它。其他参数：`--bot-name 名字`、`--admins QQ号`、`--private-users QQ号`。

GSCore 和看板不在同一台机器上时，把插件配置里的 `vikunja_api` 改成 `https://todo.example.com/api/v1`。

## 方案 A：手动部署

脚本跑不通，或者想自己掌控每一步时，照下面做。示例里的域名、目录、端口都换成你自己的。

**1. 起 Vikunja**

```bash
sudo mkdir -p /opt/iunoddl-vikunja/data/db /opt/iunoddl-vikunja/data/files
sudo chown 1000:1000 /opt/iunoddl-vikunja/data/db /opt/iunoddl-vikunja/data/files  # 镜像里以 uid 1000 运行
sudo cp site/compose.yaml /opt/iunoddl-vikunja/
sudo install -m 600 /dev/null /opt/iunoddl-vikunja/.env
printf 'VIKUNJA_PUBLIC_URL=https://todo.example.com/\nVIKUNJA_SECRET=%s\nVIKUNJA_PORT=3456\n' "$(openssl rand -hex 32)" \
    | sudo tee /opt/iunoddl-vikunja/.env >/dev/null
cd /opt/iunoddl-vikunja && sudo docker compose up -d
curl -s http://127.0.0.1:3456/api/v1/info   # 能看到 "version":"v2.6.0" 就是起来了
```

**2. 建管理员**（注册是关着的，只能用命令行建，会提示输入密码）

```bash
sudo docker compose exec vikunja /app/vikunja/vikunja user create -u ddladmin -e you@example.com
```

**3. 建项目、分享和令牌**，二选一：

- 用脚本：`sudo env VIKUNJA_ADMIN_PASSWORD='管理员密码' python3 site/vikunja_bootstrap.py --api http://127.0.0.1:3456/api/v1 --username ddladmin --out /opt/iunoddl-vikunja/bootstrap.json`，结果里有项目编号、视图编号、分享哈希和令牌；
- 在网页上：等第 5 步 nginx 配好后登录管理后台，
  - 新建项目「DDL」，打开后网址是 `/projects/<项目编号>/<视图编号>`，默认打开的就是「列表」视图，记下这两个数字；
  - 在项目菜单里选「共享项目」→「创建共享链接」，权限选「只读」，不设密码。生成的链接 `/share/<哈希>/auth` 里那一段就是分享哈希；
  - 「设置」→「API 令牌」→「创建令牌」，只勾这些权限：`projects_views_tasks` 的 read_all；`tasks` 的 read_one、create、update、delete；`labels` 的 read_all、create；`tasks_labels` 的 update_bulk；`tasks_comments` 的 create。过期时间选「永不过期」（令牌过期后插件就访问不了看板）。令牌只显示一次，马上抄下来。

**4. 放网页**，把模板里的四个占位符换掉：

```bash
sudo mkdir -p /srv/www/ddl.example.com/.well-known/acme-challenge
sed -e 's/__PROJECT_ID__/项目编号/g' -e 's/__VIEW_ID__/视图编号/g' -e 's/__SHARE_HASH__/分享哈希/g' \
    -e 's#__TODO_URL__#https://todo.example.com/#g' site/index.html | sudo tee /srv/www/ddl.example.com/index.html >/dev/null
```

**5. 配 nginx 和证书**。两个模板里的占位符：`__DDL_DOMAIN__`、`__TODO_DOMAIN__`、`__WEB_ROOT__`（网页目录）、`__CERT_DIR__`（证书目录）、`__VIKUNJA_PORT__`、`__FRAME_ANCESTORS__`（谁能嵌入看板，一般填 `'self'`）。

```bash
fill() {
    sed -e 's/__DDL_DOMAIN__/ddl.example.com/g' -e 's/__TODO_DOMAIN__/todo.example.com/g' \
        -e 's#__WEB_ROOT__#/srv/www/ddl.example.com#g' -e 's#__CERT_DIR__#/etc/letsencrypt/live/ddl.example.com#g' \
        -e 's/__VIKUNJA_PORT__/3456/g' -e "s/__FRAME_ANCESTORS__/'self'/g" "$1"
}
fill site/nginx/iunoddl-http.conf | sudo tee /etc/nginx/sites-available/iunoddl-http.conf >/dev/null
sudo ln -s /etc/nginx/sites-available/iunoddl-http.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot certonly --webroot -w /srv/www/ddl.example.com --cert-name ddl.example.com \
    -d ddl.example.com -d todo.example.com --deploy-hook "systemctl reload nginx"
fill site/nginx/iunoddl-https.conf | sudo tee /etc/nginx/sites-available/iunoddl-https.conf >/dev/null
sudo ln -s /etc/nginx/sites-available/iunoddl-https.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

**6. 装插件**：把 `plugin/IunoDDL` 整个复制到 `gsuid_core/plugins/` 下，重启 GSCore。第一次加载会生成 `data/IunoDDL/config.json`，把 `vikunja_token`、`project_id`、`view_id`、`board_url`、`allowed_groups` 填好就能用，改配置不用重启。每个字段的意思写在文件里的 `_说明` 和插件的 README 里。

## 安全说明

- 看板是公开的：知道网址的人都能看到所有没完成的作业，包括要求和附件。别往这个项目里放不想公开的东西。
- 插件令牌只有上面列的那几项权限，读不了别的项目，也删不了项目、改不了分享。令牌和管理员密码都在 root 才能读的文件里（权限 600），别外传。
- Vikunja 的签名密钥在 `.env` 里（权限 600），泄露了别人就能伪造登录。
- 注册默认关着，新用户只能管理员用命令行建。
- 部署脚本建管理员时，密码会作为命令行参数在 `docker exec` 里出现一瞬间，同一台机器上的其他用户理论上能看到；介意的话按方案 A 第 2 步手动建。

## 测试情况

- **插件**：用替身 gsuid_core 测过加载、默认配置、群权限、改名字和看板网址；用最小权限的令牌对 Vikunja 2.6.0 测过新建、修改、删除、撤销，越权操作都被拒绝。作者自己的机器人（GSCore 0.10.7）已经在用这一版，并且经隔离测试桥在线上跑过一轮回归测试：文字帮助、帮助图、列表、详情、草稿、未开放的群不回应、起草后取消、确认写入后撤销、AI 查询工具，15 项全部通过。
- **部署和安装脚本**：在全新的 Ubuntu 24.04 容器里按方案 B 从头跑过（docker 29.1.3、nginx 1.24.0，真的用 docker compose 起 Vikunja），包括首次部署、原样重跑、装插件；另外用替身环境测过域名被占用时停下、证书有问题时撤回 nginx 配置。
- **还没测过的**：certbot 真正申请证书（需要公网能访问的真域名，测试时用的是自签名证书和 `--skip-certbot`）；nginx 由真正的 systemd 管理（测试容器里没跑 systemd，`systemctl reload nginx` 用等价的 `nginx -s reload` 代替）。

## 已知限制

- 时区固定是 Asia/Shanghai。

## 还没做的

- 回归测试补上修改、合并、删除三种草稿
- 在有真域名的服务器上实测 certbot 申请证书那一步

## 素材

帮助图里的图标取自「呜哇小站」表情图鉴里的尤诺表情包（<https://emoji.wuwa.games/archives?character=%E5%B0%A4%E8%AF%BA>）：15 个来自「企划/Solar5」，2 个来自「画师/雾雪」。每个图标取自哪张表情的第几帧，记在 `plugin/IunoDDL/IunoDDL/iunoddl_help/icons/sources.json` 里。

帮助图的横幅是另外合成的：夜空、星星和月亮都是程序画的，人物取自「画师/雾雪」表情包里的「哈气」第 0 帧，原样放上去，没有缩放、裁切或改动。想换成自己的横幅，见插件 README。

按该站的说明，这些表情**仅供个人、非商业使用**，禁止倒卖、付费分发、打包牟利或以其他方式借助它们牟利。

## 许可证

代码按 [GPL-3.0-or-later](LICENSE) 发布，和 GSCore（gsuid_core）一致，插件直接引用了它的代码。帮助图里的图片不在这个许可范围内：它们的版权归原作者，只能按上面「素材」一节的条件使用。GPL 允许商用代码，但如果你要商用，请先把这些图片换成自己有权使用的图。
