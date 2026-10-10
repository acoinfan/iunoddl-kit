PROMPTS = []
REPLY = ['{"items": []}']


class _S:
    def close(self):
        pass


class _A:
    def __init__(self, kw):
        self.kw = kw
        self._session_logger = _S()

    async def run(self, prompt, return_mode=None):
        PROMPTS.append((self.kw["system_prompt"], prompt))
        return REPLY[0]


def create_agent(**kw):
    return _A(kw)
