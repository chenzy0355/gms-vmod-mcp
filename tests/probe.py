"""手工探测：直接向 MCP 服务进程发原始 JSON-RPC，观察 stdout/stderr。"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
env = dict(os.environ)
env.update({"PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1"})
env.pop("PYTHONWARNINGS", None)
PY = sys.executable

p = subprocess.Popen([PY, "-m", "mfmcp.server"], stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                     env=env, cwd=str(ROOT))


def send(obj):
    p.stdin.write((json.dumps(obj) + "\n").encode())
    p.stdin.flush()


send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
      "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                 "clientInfo": {"name": "probe", "version": "1"}}})
send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})

import time
time.sleep(5)
p.stdin.close()
out, err = p.communicate(timeout=20)

print("=== STDOUT ===")
for line in out.decode("utf-8", "replace").splitlines():
    print(line[:200])
print("=== STDERR ===")
print(err.decode("utf-8", "replace")[-3000:])
print("=== returncode ===", p.returncode)
