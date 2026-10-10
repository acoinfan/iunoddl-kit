"""一条命令跑完全部测试：python3 tests/run_all.py。每个测试单独开一个进程，互不影响。"""

import sys
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = ["test_basic.py", "test_modes.py", "test_homework_golden.py"]

failed = []
for name in TESTS:
    result = subprocess.run([sys.executable, str(HERE / name)], capture_output=True, text=True)
    last = (result.stdout.strip().splitlines() or [""])[-1]
    if result.returncode == 0:
        print(f"✓ {name}：{last}")
    else:
        failed.append(name)
        print(f"✗ {name}\n{result.stdout}{result.stderr}")
sys.exit(1 if failed else 0)
