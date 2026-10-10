class Bot:
    def __init__(self, ev=None):
        self.ev = ev
        self.sent = []

    async def send(self, msg):
        self.sent.append(msg)
