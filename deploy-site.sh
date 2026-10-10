#!/usr/bin/env bash
# 一键部署 DDL 看板：Vikunja（docker）+ 只读看板前端 + nginx + Let's Encrypt 证书。
# 适用于 Ubuntu 22.04 / 24.04，需要 root。可以重复运行：已有的密钥、管理员、项目、分享和令牌都会沿用。
set -Eeuo pipefail

KIT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

DDL_DOMAIN=""
TODO_DOMAIN=""
EMAIL=""
INSTALL_DIR=/opt/iunoddl-vikunja
WEB_ROOT=""
PORT=3456
ADMIN_USER=ddladmin
PROJECT=DDL
FRAME_ANCESTORS="'self'"
CERT_DIR=""
SKIP_CERTBOT=0
NGINX_DIR=/etc/nginx

usage() {
    cat <<'EOF'
用法：sudo ./deploy-site.sh --ddl 看板域名 --todo 后台域名 --email 邮箱 [选项]

必填：
  --ddl DOMAIN           公开只读看板的域名，例如 ddl.example.com
  --todo DOMAIN          Vikunja 管理后台的域名，例如 todo.example.com
  --email EMAIL          申请证书用的邮箱，也是 Vikunja 管理员的邮箱

选项：
  --dir DIR              Vikunja 的安装目录，默认 /opt/iunoddl-vikunja
  --web-root DIR         看板网页放在哪里，默认 /srv/www/<看板域名>
  --port PORT            Vikunja 在本机监听的端口，默认 3456
  --admin USER           Vikunja 管理员用户名，默认 ddladmin
  --project NAME         看板项目的名字，默认 DDL
  --frame-ancestors SRC  允许嵌入看板的网站，默认 'self'；例如 "'self' https://blog.example.com"
  --skip-certbot         不申请证书，用 --cert-dir 里已有的 fullchain.pem 和 privkey.pem
  --cert-dir DIR         和 --skip-certbot 一起用：已有证书的目录，默认 /etc/letsencrypt/live/<看板域名>
  --nginx-dir DIR        nginx 配置目录，默认 /etc/nginx

两个域名都要先解析到这台服务器，80 和 443 端口要能从外网访问。
EOF
}

say() { printf '\n==> %s\n' "$*"; }
die() {
    printf '\n错误：%s\n' "$*" >&2
    exit 1
}
trap 'printf "\n第 %s 行出错（退出码 %s），部署停在这一步。修好后可以直接重跑。\n" "$LINENO" "$?" >&2' ERR

while [ $# -gt 0 ]; do
    case "$1" in
        --ddl) DDL_DOMAIN=${2:?}; shift 2 ;;
        --todo) TODO_DOMAIN=${2:?}; shift 2 ;;
        --email) EMAIL=${2:?}; shift 2 ;;
        --dir) INSTALL_DIR=${2:?}; shift 2 ;;
        --web-root) WEB_ROOT=${2:?}; shift 2 ;;
        --port) PORT=${2:?}; shift 2 ;;
        --admin) ADMIN_USER=${2:?}; shift 2 ;;
        --project) PROJECT=${2:?}; shift 2 ;;
        --frame-ancestors) FRAME_ANCESTORS=${2:?}; shift 2 ;;
        --skip-certbot) SKIP_CERTBOT=1; shift ;;
        --cert-dir) CERT_DIR=${2:?}; shift 2 ;;
        --nginx-dir) NGINX_DIR=${2:?}; shift 2 ;;
        -h | --help) usage; exit 0 ;;
        *) usage >&2; die "不认识的参数：$1" ;;
    esac
done

# ---------------------------------------------------------------- 检查

say "检查参数和环境"
[ -n "$DDL_DOMAIN" ] && [ -n "$TODO_DOMAIN" ] && [ -n "$EMAIL" ] || { usage >&2; die "--ddl、--todo、--email 都要填"; }
DOMAIN_RE='^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$'
[[ $DDL_DOMAIN =~ $DOMAIN_RE ]] || die "看板域名格式不对：$DDL_DOMAIN"
[[ $TODO_DOMAIN =~ $DOMAIN_RE ]] || die "后台域名格式不对：$TODO_DOMAIN"
[ "$DDL_DOMAIN" != "$TODO_DOMAIN" ] || die "看板和后台要用两个不同的域名"
[[ $EMAIL == *@*.* ]] || die "邮箱格式不对：$EMAIL"
[[ $PORT =~ ^[0-9]+$ ]] && [ "$PORT" -ge 1024 ] && [ "$PORT" -le 65535 ] || die "端口要在 1024 到 65535 之间"
[[ $ADMIN_USER =~ ^[A-Za-z0-9_.-]+$ ]] || die "管理员用户名只能用字母、数字和 _ . -"
[[ $FRAME_ANCESTORS != *[\"\;]* ]] || die "--frame-ancestors 里不能有双引号和分号"
WEB_ROOT=${WEB_ROOT:-/srv/www/$DDL_DOMAIN}
if [ -n "$CERT_DIR" ] && [ "$SKIP_CERTBOT" = 0 ] && [ "$CERT_DIR" != "/etc/letsencrypt/live/$DDL_DOMAIN" ]; then
    die "--cert-dir 要和 --skip-certbot 一起用：申请新证书时 certbot 只会存到 /etc/letsencrypt/live/$DDL_DOMAIN"
fi
CERT_DIR=${CERT_DIR:-/etc/letsencrypt/live/$DDL_DOMAIN}
for d in "$INSTALL_DIR" "$WEB_ROOT" "$CERT_DIR"; do
    [[ $d == /* && $d != *[[:space:]\;\"\'\$\`\\]* ]] || die "目录要写绝对路径，且不能有空格和引号：$d"
done

[ "$(id -u)" = 0 ] || die "要用 root 运行：sudo $0 ..."
for c in docker nginx python3 openssl curl ss systemctl; do
    command -v "$c" >/dev/null || die "缺少 $c。Ubuntu 上可以装：apt install docker.io docker-compose-v2 nginx certbot python3 openssl curl"
done
docker compose version >/dev/null 2>&1 || die "缺少 docker compose 插件：apt install docker-compose-v2"
if [ "$SKIP_CERTBOT" = 0 ]; then
    command -v certbot >/dev/null || die "缺少 certbot：apt install certbot；已有证书的话加 --skip-certbot --cert-dir 目录"
else
    [ -s "$CERT_DIR/fullchain.pem" ] && [ -s "$CERT_DIR/privkey.pem" ] || die "$CERT_DIR 里没有 fullchain.pem 和 privkey.pem"
fi

if [ -d "$NGINX_DIR/sites-available" ] && [ -d "$NGINX_DIR/sites-enabled" ]; then
    CONF_DIR=$NGINX_DIR/sites-available
    LINK_DIR=$NGINX_DIR/sites-enabled
else
    CONF_DIR=$NGINX_DIR/conf.d
    LINK_DIR=""
fi
[ -d "$CONF_DIR" ] || die "找不到 nginx 配置目录 $CONF_DIR"
HTTP_CONF=$CONF_DIR/iunoddl-http.conf
HTTPS_CONF=$CONF_DIR/iunoddl-https.conf

# 别的站点已经占用了这两个域名的话，nginx 会冲突，先停下来让人处理
for domain in "$DDL_DOMAIN" "$TODO_DOMAIN"; do
    other=$(grep -rlE "server_name[^;]*[[:space:]]${domain//./\\.}([[:space:]]|;)" "$NGINX_DIR" 2>/dev/null \
        | grep -v -e '/iunoddl-http\.conf$' -e '/iunoddl-https\.conf$' || true)
    [ -z "$other" ] || die "nginx 里已经有站点用了 $domain：$other"
done
nginx -t -q 2>/dev/null || die "nginx 现有配置本身就检查不过（nginx -t），先修好再部署"

if [ -f "$INSTALL_DIR/.env" ] && grep -qx "VIKUNJA_PORT=$PORT" "$INSTALL_DIR/.env"; then
    : # 重跑：端口是自己的 Vikunja 在用
elif ss -Hltn "sport = :$PORT" | grep -q .; then
    die "本机端口 $PORT 已经被占用，换一个：--port 端口"
fi

for domain in "$DDL_DOMAIN" "$TODO_DOMAIN"; do
    getent hosts "$domain" >/dev/null || echo "提醒：这台机器解析不到 $domain，申请证书前要先把域名解析过来"
done

# ---------------------------------------------------------------- Vikunja

say "启动 Vikunja（$INSTALL_DIR）"
install -d -m 755 "$INSTALL_DIR"
install -d -m 750 -o 1000 -g 1000 "$INSTALL_DIR/data/db" "$INSTALL_DIR/data/files"
install -m 644 "$KIT_DIR/site/compose.yaml" "$INSTALL_DIR/compose.yaml"
SECRET=""
if [ -f "$INSTALL_DIR/.env" ]; then
    SECRET=$(sed -n 's/^VIKUNJA_SECRET=//p' "$INSTALL_DIR/.env")
fi
SECRET=${SECRET:-$(openssl rand -hex 32)}
(
    umask 077
    printf 'VIKUNJA_PUBLIC_URL=https://%s/\nVIKUNJA_SECRET=%s\nVIKUNJA_PORT=%s\n' "$TODO_DOMAIN" "$SECRET" "$PORT" \
        >"$INSTALL_DIR/.env"
)
chmod 600 "$INSTALL_DIR/.env"
compose() { docker compose --project-directory "$INSTALL_DIR" -f "$INSTALL_DIR/compose.yaml" "$@"; }
compose up -d
API=http://127.0.0.1:$PORT/api/v1
for _ in $(seq 60); do
    curl -fsS --noproxy 127.0.0.1 "$API/info" >/dev/null 2>&1 && break
    sleep 2
done
curl -fsS --noproxy 127.0.0.1 "$API/info" >/dev/null || die "Vikunja 两分钟内没起来，看看日志：docker compose --project-directory $INSTALL_DIR logs"

PASS_FILE=$INSTALL_DIR/admin-password
if [ ! -s "$PASS_FILE" ]; then
    say "创建 Vikunja 管理员 $ADMIN_USER"
    (
        umask 077
        openssl rand -base64 24 | tr -d '/+=\n' | cut -c1-24 >"$PASS_FILE"
    )
    compose exec -T vikunja /app/vikunja/vikunja user create \
        -u "$ADMIN_USER" -e "$EMAIL" -p "$(cat "$PASS_FILE")" >/dev/null \
        || { rm -f "$PASS_FILE"; die "创建管理员失败；如果这个用户名已经存在，换一个 --admin，或者把它的密码写进 $PASS_FILE 再重跑"; }
fi
chmod 600 "$PASS_FILE"

say "建看板项目、只读分享和插件令牌"
VIKUNJA_ADMIN_PASSWORD=$(cat "$PASS_FILE") python3 -I "$KIT_DIR/site/vikunja_bootstrap.py" \
    --api "$API" --username "$ADMIN_USER" --project "$PROJECT" --out "$INSTALL_DIR/bootstrap.json"

# ---------------------------------------------------------------- 网页和 nginx

# 把模板里的 __名字__ 换成 TPL_名字 环境变量的值；有没换掉的占位符就报错
render() {
    python3 -I - "$1" "$2" <<'PY'
import os, re, sys
src, dst = sys.argv[1], sys.argv[2]
text = open(src, encoding="utf-8").read()
text = re.sub(r"__([A-Z_]+?)__", lambda m: os.environ["TPL_" + m.group(1)], text)
fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write(text)
PY
}

say "生成看板网页（$WEB_ROOT）"
eval "$(python3 -I - "$INSTALL_DIR/bootstrap.json" <<'PY'
import json, re, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert isinstance(d["project_id"], int) and isinstance(d["view_id"], int)
assert re.fullmatch(r"[A-Za-z0-9_-]+", d["share_hash"])
print(f"TPL_PROJECT_ID={d['project_id']} TPL_VIEW_ID={d['view_id']} TPL_SHARE_HASH={d['share_hash']}")
PY
)"
export TPL_PROJECT_ID TPL_VIEW_ID TPL_SHARE_HASH
export TPL_TODO_URL=https://$TODO_DOMAIN/ TPL_DDL_DOMAIN=$DDL_DOMAIN TPL_TODO_DOMAIN=$TODO_DOMAIN
export TPL_WEB_ROOT=$WEB_ROOT TPL_CERT_DIR=$CERT_DIR TPL_VIKUNJA_PORT=$PORT TPL_FRAME_ANCESTORS=$FRAME_ANCESTORS
install -d -m 755 "$WEB_ROOT" "$WEB_ROOT/.well-known/acme-challenge"
render "$KIT_DIR/site/index.html" "$WEB_ROOT/index.html"

# 写进去一份 nginx 配置并检查；检查不过就撤掉，让 nginx 回到原来的样子
apply_conf() {
    local tpl=$1 dst=$2 backup=""
    if [ -f "$dst" ]; then
        backup=$(mktemp)
        cp -p "$dst" "$backup"
    fi
    render "$tpl" "$dst"
    [ -z "$LINK_DIR" ] || ln -sfn "$dst" "$LINK_DIR/$(basename "$dst")"
    if ! nginx -t -q; then
        if [ -n "$backup" ]; then
            mv "$backup" "$dst"
        else
            rm -f "$dst"
            [ -z "$LINK_DIR" ] || rm -f "$LINK_DIR/$(basename "$dst")"
        fi
        die "新的 nginx 配置检查不过，已经撤回：$dst"
    fi
    [ -z "$backup" ] || rm -f "$backup"
    systemctl reload nginx
}

# reload 只是给 nginx 主进程发个信号就返回，新配置要稍等才生效；等它真的生效再往下走
wait_until() {
    local what=$1
    shift
    for _ in $(seq 30); do
        "$@" && return 0
        sleep 0.5
    done
    die "nginx 重载后 15 秒内${what}还没生效，查一下 /var/log/nginx/error.log"
}
# 80 端口：验证目录里放一个随机内容的探针，能原样读回来才算生效，certbot 就靠这个目录验证域名
acme_ready() {
    [ "$(curl -s --noproxy '*' -H "Host: $DDL_DOMAIN" "http://127.0.0.1/.well-known/acme-challenge/$PROBE" || true)" = "$PROBE" ]
}
https_ready() {
    [ "$(curl -sk --noproxy '*' -o /dev/null -w '%{http_code}' --resolve "$DDL_DOMAIN:443:127.0.0.1" \
        "https://$DDL_DOMAIN/" || true)" = 200 ]
}

say "配置 nginx 的 80 端口"
apply_conf "$KIT_DIR/site/nginx/iunoddl-http.conf" "$HTTP_CONF"
PROBE=iunoddl-probe-$(openssl rand -hex 8)
printf '%s' "$PROBE" >"$WEB_ROOT/.well-known/acme-challenge/$PROBE"
wait_until "80 端口的配置" acme_ready
rm -f "$WEB_ROOT/.well-known/acme-challenge/$PROBE"

if [ "$SKIP_CERTBOT" = 0 ]; then
    say "申请证书（$DDL_DOMAIN、$TODO_DOMAIN）"
    certbot certonly --webroot -w "$WEB_ROOT" --cert-name "$DDL_DOMAIN" -d "$DDL_DOMAIN" -d "$TODO_DOMAIN" \
        --email "$EMAIL" --agree-tos --no-eff-email --non-interactive --keep-until-expiring \
        --deploy-hook "systemctl reload nginx"
fi

say "配置 nginx 的 443 端口"
apply_conf "$KIT_DIR/site/nginx/iunoddl-https.conf" "$HTTPS_CONF"
wait_until "443 端口的配置" https_ready

# ---------------------------------------------------------------- 插件配置和自检

say "生成插件配置"
PLUGIN_CONF=$INSTALL_DIR/iunoddl-config.json
python3 -I - "$INSTALL_DIR/bootstrap.json" "$PLUGIN_CONF" "$API" "https://$DDL_DOMAIN/" <<'PY'
import json, os, sys
src, dst, api, board = sys.argv[1:]
d = json.load(open(src, encoding="utf-8"))
cfg = {"vikunja_api": api, "vikunja_token": d["token"], "project_id": d["project_id"],
       "view_id": d["view_id"], "board_url": board}
fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
PY
chmod 600 "$PLUGIN_CONF"

say "自检"
check() {
    local name=$1 url=$2 host=$3
    local code=""
    for _ in $(seq 10); do
        code=$(curl -sk --noproxy '*' -o /dev/null -w '%{http_code}' --resolve "$host:443:127.0.0.1" "$url" || true)
        [ "$code" = 200 ] && break
        sleep 1
    done
    printf '  %-28s %s\n' "$name" "$code"
    [ "$code" = 200 ]
}
ok=1
check "看板首页" "https://$DDL_DOMAIN/" "$DDL_DOMAIN" || ok=0
check "看板接口" "https://$DDL_DOMAIN/api/v1/info" "$DDL_DOMAIN" || ok=0
check "管理后台" "https://$TODO_DOMAIN/" "$TODO_DOMAIN" || ok=0
[ "$ok" = 1 ] || echo "提醒：自检有不是 200 的，看看上面哪一项，再查 nginx 和 Vikunja 的日志"

cat <<EOF

部署完成。
  看板：      https://$DDL_DOMAIN/
  管理后台：  https://$TODO_DOMAIN/（用户名 $ADMIN_USER，密码：sudo cat $PASS_FILE）
  插件配置：  $PLUGIN_CONF（里面有令牌，别外传）

下一步：把插件装进 GSCore，例如
  sudo ./install-plugin.sh --gscore /opt/gsuid_core --config $PLUGIN_CONF --groups 群号1,群号2
EOF
