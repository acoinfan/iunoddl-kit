# 测试

不用装 GSCore 也能测插件：`stubs/` 里是替身的 `gsuid_core` 和 `pydantic_ai`，只实现插件用到的那几个接口；替身模型会记下发给它的提示词，返回预先设好的回复。

```bash
python3 -m venv .venv && .venv/bin/pip install -r tests/requirements.txt
.venv/bin/python tests/run_all.py
```

| 文件 | 测什么 |
| --- | --- |
| `test_basic.py` | 默认配置、权限、未开放的群、横幅、改名、看板网址、AI 帮助 |
| `test_modes.py` | `mode` 和 `extra_prompt` 的读取和回退、提示词怎么拼、两种模式认哪些字段名和卡片 |
| `test_homework_golden.py` | 作业模式下帮助、草稿、列表、详情、看板描述、提示语、发给模型的提示词等 176 项输出，和 `golden/homework-0.2.0.json` 逐字比对 |

作业模式的输出要和以前逐字相同，这样已经在用作业模式的机器人升级后不会有任何变化。如果有意修改作业模式的措辞，运行 `python tests/test_homework_golden.py --update` 重新生成标准答案，并在提交说明里写清楚为什么改。

这些测试用的都是替身，真模型和真 Vikunja 要在实际部署里测。
