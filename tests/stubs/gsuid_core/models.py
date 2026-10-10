class Event:
    def __init__(self, **kw):
        self.user_id = "10001"
        self.group_id = None
        self.sender = {"nickname": "测试"}
        self.user_pm = 6
        self.text = ""
        self.node = []
        self.reply = None
        self.raw_text = ""
        self.__dict__.update(kw)
