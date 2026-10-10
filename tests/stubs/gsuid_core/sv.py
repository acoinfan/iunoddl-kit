class Plugins:
    def __init__(self, **kw):
        pass


class SV:
    def __init__(self, *a, **kw):
        pass

    def on_command(self, *a, **kw):
        return lambda f: f
