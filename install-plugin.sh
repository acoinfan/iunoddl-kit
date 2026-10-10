#!/usr/bin/env bash
# 把 IunoDDL 插件装进 GSCore，并写好 data/IunoDDL/config.json。升级插件也用它，可以重复运行。
# 已有的配置会保留，只覆盖这次用参数或 --config 给出的字段。
set -Eeuo pipefail

KIT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

GSCORE=""
CONFIG=""
BOT_NAME=""
MODE=""
GROUP_IDS=""
ADMINS=""
PRIVATE_USERS=""
SET_GROUPS=0
SET_ADMINS=0
SET_PRIVATE=0

usage() {
    cat <<'EOF'
用法：./install-plugin.sh --gscore GSCore目录 [选项]

  --gscore DIR           GSCore 的根目录（里面有 gsuid_core/plugins 和 data）
  --config FILE          deploy-site.sh 生成的 iunoddl-config.json，带看板地址和令牌
  --bot-name NAME        机器人的自称，默认「尤诺」
  --mode MODE            general：记所有有截止时间的事；homework：只记作业，措辞也按作业来
  --groups 群号,群号     开放 DDL 功能的 QQ 群；写 --groups "" 表示一个都不开
  --admins QQ号,QQ号     能撤销别人操作、能私聊使用的 QQ 号
  --private-users QQ号   另外允许私聊使用的 QQ 号

装完重启 GSCore 才会加载插件。
EOF
}

die() {
    printf '\n错误：%s\n' "$*" >&2
    exit 1
}
trap 'printf "\n第 %s 行出错（退出码 %s），安装停在这一步。\n" "$LINENO" "$?" >&2' ERR

while [ $# -gt 0 ]; do
    case "$1" in
        --gscore) GSCORE=${2:?}; shift 2 ;;
        --config) CONFIG=${2:?}; shift 2 ;;
        --bot-name) BOT_NAME=${2:?}; shift 2 ;;
        --mode) MODE=${2:?}; shift 2 ;;
        --groups) GROUP_IDS=${2-}; SET_GROUPS=1; shift 2 ;;
        --admins) ADMINS=${2-}; SET_ADMINS=1; shift 2 ;;
        --private-users) PRIVATE_USERS=${2-}; SET_PRIVATE=1; shift 2 ;;
        -h | --help) usage; exit 0 ;;
        *) usage >&2; die "不认识的参数：$1" ;;
    esac
done

[ -n "$GSCORE" ] || { usage >&2; die "要用 --gscore 指定 GSCore 目录"; }
[ -d "$GSCORE" ] || die "目录不存在：$GSCORE"
GSCORE=$(cd "$GSCORE" && pwd)
PLUGINS=$GSCORE/gsuid_core/plugins
[ -d "$PLUGINS" ] || die "$GSCORE 下面没有 gsuid_core/plugins，确认一下是不是 GSCore 的根目录"
[ -z "$CONFIG" ] || [ -r "$CONFIG" ] || die "读不到 $CONFIG"
[ -z "$MODE" ] || [ "$MODE" = general ] || [ "$MODE" = homework ] || die "--mode 只能是 general 或 homework"
for list in "$GROUP_IDS" "$ADMINS" "$PRIVATE_USERS"; do
    [[ $list =~ ^[0-9,[:space:]]*$ ]] || die "群号和 QQ 号只能是数字，用逗号分开：$list"
done

# 插件要用 GSCore 的 AI 模块（0.10.7 起有），先看一眼，不导入、不运行 GSCore 的代码
version=$(sed -n 's/^version *= *"\(.*\)"/\1/p' "$GSCORE/pyproject.toml" 2>/dev/null | head -1)
[ -f "$GSCORE/gsuid_core/ai_core/register.py" ] || die "这个 GSCore（${version:-版本未知}）没有 ai_core 模块，插件需要 0.10.7 或更新的版本"
echo "GSCore 版本：${version:-未知}（插件按 0.10.7 测试）"

# 文件归 GSCore 目录的主人，免得用 sudo 装完 GSCore 读写不了
OWNER=$(stat -c %u:%g "$GSCORE")
own() { [ "$(id -u)" != 0 ] || chown -R "$OWNER" "$@"; }

echo "安装插件到 $PLUGINS/IunoDDL"
# 先在 plugins 外面准备好新版本再换进去，plugins 里的目录都会被 GSCore 当成插件加载
WORK=$GSCORE/.iunoddl-backups
mkdir -p "$WORK"
staging=$(mktemp -d "$WORK/staging.XXXXXX")
cp -r "$KIT_DIR/plugin/IunoDDL/." "$staging/"
find "$staging" -name __pycache__ -type d -prune -exec rm -rf {} +
chmod 755 "$staging"
if [ -d "$PLUGINS/IunoDDL" ]; then
    # 名字带上进程号：同一秒里连装两次也不会撞名
    backup=$WORK/IunoDDL-$(date +%Y%m%d%H%M%S)-$$
    mv "$PLUGINS/IunoDDL" "$backup"
    echo "旧版本已挪到 $backup"
fi
mv "$staging" "$PLUGINS/IunoDDL"
own "$PLUGINS/IunoDDL"

DATA=$GSCORE/data/IunoDDL
echo "写配置 $DATA/config.json"
mkdir -p "$DATA"
chmod 700 "$DATA"
export BOT_NAME MODE GROUP_IDS ADMINS PRIVATE_USERS SET_GROUPS SET_ADMINS SET_PRIVATE
python3 -I - "$KIT_DIR/plugin/IunoDDL/IunoDDL/utils/store.py" "$DATA/config.json" "$CONFIG" <<'PY'
import ast, json, os, sys
store, dst, deploy = sys.argv[1:]
env = os.environ

# 默认值和字段说明直接从插件源码里读，免得两边写的不一样
literals = {}
for node in ast.parse(open(store, encoding="utf-8").read()).body:
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        literals[node.target.id] = node.value
    elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        literals[node.targets[0].id] = node.value
defaults = ast.literal_eval(literals["DEFAULT_CONFIG"])
doc = ast.literal_eval(literals["CONFIG_HELP"])

cfg = dict(defaults)
if os.path.exists(dst):
    cfg.update(json.load(open(dst, encoding="utf-8")))
if deploy:
    cfg.update(json.load(open(deploy, encoding="utf-8")))
if env["BOT_NAME"].strip():
    cfg["bot_name"] = env["BOT_NAME"].strip()
if env["MODE"]:
    cfg["mode"] = env["MODE"]
for flag, key, value in (
    ("SET_GROUPS", "allowed_groups", "GROUP_IDS"),
    ("SET_ADMINS", "admins", "ADMINS"),
    ("SET_PRIVATE", "private_users", "PRIVATE_USERS"),
):
    if env[flag] == "1":
        cfg[key] = [x.strip() for x in env[value].split(",") if x.strip()]
cfg.pop("_说明", None)

fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump({"_说明": doc, **cfg}, f, ensure_ascii=False, indent=2)
print(f"  自称：{cfg['bot_name']}，模式：{cfg['mode']}")
print(f"  看板：{cfg['board_url'] or '（没填）'}")
print(f"  项目 {cfg['project_id']}，视图 {cfg['view_id']}，令牌{'已填' if cfg['vikunja_token'] else '没填'}")
print(f"  开放的群：{'、'.join(map(str, cfg['allowed_groups'])) or '（一个都没开）'}")
PY
chmod 600 "$DATA/config.json"
own "$DATA"

cat <<EOF

装好了。重启 GSCore 后生效，例如 sudo systemctl restart gscore（按你自己的启动方式来）。
之后改配置直接编辑 $DATA/config.json，不用重启；改了 bot_name 或 board_url 想让帮助图跟着变，才需要重启。
EOF
