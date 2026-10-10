CALLS = []


async def get_new_help(**kw):
    CALLS.append(kw)
    return b"IMG"
