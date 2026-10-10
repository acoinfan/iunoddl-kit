class AgentHookPoint:
    ASSEMBLE_TOOLS = "a"
    AFTER_CONTEXT = "b"


class AgentHookContext:
    pass


def on_agent_hook(*a, **kw):
    return lambda f: f
