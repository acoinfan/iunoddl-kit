from typing import TypedDict


class PluginSV(TypedDict, total=False):
    name: str


class PluginHelp(TypedDict, total=False):
    desc: str
