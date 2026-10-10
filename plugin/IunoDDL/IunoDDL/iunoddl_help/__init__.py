"""DDL 小本本的帮助图：使用随插件提供的固定静态图标。"""

from pathlib import Path
from urllib.parse import urlparse

from PIL import Image

from gsuid_core.help.model import PluginSV, PluginHelp
from gsuid_core.help.utils import register_help
from gsuid_core.help.draw_new_plugin_help import get_new_help

from ..utils.core import term, by_mode, bot_name
from ..utils.store import MAIN_PATH

ICON_DIR = Path(__file__).parent / "icons"
BANNER = Path(__file__).parent / "banner.jpg"
# 想换横幅就放一张图到 data/IunoDDL/banner.jpg，升级插件也不会被覆盖
CUSTOM_BANNER = MAIN_PATH / "banner.jpg"


class DDLHelpItem(PluginSV):
    icon: Path


def _plugin_icon() -> Image.Image:
    with Image.open(ICON_DIR / "plugin.png") as icon:
        return icon.copy()


def _item(name: str, eg: str, icon_name: str, highlight: int = 6) -> DDLHelpItem:
    return {
        "name": name,
        "desc": name,
        "eg": eg,
        "highlight": highlight,
        "need_ck": False,
        "need_sk": False,
        "need_admin": False,
        "icon": ICON_DIR / f"{icon_name}.png",
    }


def _help_data(board_url: str) -> dict[str, PluginHelp]:
    host = urlparse(board_url).netloc
    item = term("item")
    return {
        by_mode("记 DDL", "记作业"): {
            "desc": by_mode("把通知或一句话变成看板上的 DDL", "把作业通知变成看板上的 DDL"),
            "data": [
                _item("转发记DDL", "引用转发后发 ddl", "forward"),
                _item("打字记DDL", by_mode("ddl 明天 报名决斗赛事", "ddl 课程 作业 10/18"), "create"),
            ],
        },
        "处理草稿": {
            "desc": "长按草稿卡片「回复」最省事；不引用就要写编号，如 ddl确认T3",
            "data": [
                _item("确认写入", "引用草稿回 ddl确认", "confirm", highlight=2),
                _item("修改草稿", "引用草稿回 ddl 截止周五", "edit_draft"),
                _item("去掉一条", "引用草稿回 ddl删2", "remove_item"),
                _item("合成一条", "引用草稿回 ddl合并", "merge_draft"),
                _item("不要了", "引用草稿回 ddl取消", "cancel"),
                _item("我的草稿", "ddl草稿", "drafts"),
            ],
        },
        "改看板上的": {
            "desc": f"先 ddl#编号 调出{item}卡片，再引用卡片回复；都会先给你看对比",
            "data": [
                _item(f"调出{item}", "ddl#12", "detail"),
                _item(f"修改{item}", "引用卡片回 ddl 要改的", "edit_task"),
                _item(f"合并{item}", "ddl合并#24#25", "merge_task"),
                _item(f"删除{item}", "引用卡片回 ddl删", "delete_task"),
                _item("确认删除", "引用草稿回 ddl确认删除", "confirm_delete", highlight=0),
                _item("撤销", "ddl撤销", "undo"),
            ],
        },
        "查看": {
            "desc": f"看板 {host}" if host else f"在 QQ 里看{item}",
            "data": [
                _item(f"{item}列表", "ddl列表", "list"),
                _item("文字版帮助", "ddl文字帮助", "help"),
            ],
        },
    }


async def get_help_img(board_url: str) -> bytes:
    # 框架按插件名缓存帮助图，改了 bot_name、board_url 或横幅要重启 GSCore 才会重画
    return await get_new_help(
        plugin_name="DDL小本本",
        plugin_info={"v1.0": ""},
        plugin_icon=_plugin_icon(),
        plugin_help=_help_data(board_url),
        plugin_prefix="",
        help_mode="dark",
        banner_bg=Image.open(CUSTOM_BANNER if CUSTOM_BANNER.is_file() else BANNER),
        banner_sub_text=f"哼，{bot_name()}帮你记着呢！",
        column=3,
    )


# 挂到机器人的总帮助一览（core帮助）里
register_help("DDL小本本", "ddl帮助", _plugin_icon())
