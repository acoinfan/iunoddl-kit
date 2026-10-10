import os
from pathlib import Path


def get_res_path(_path=None):
    p = Path(os.environ["STUB_DATA"])
    p.mkdir(parents=True, exist_ok=True)
    return p
