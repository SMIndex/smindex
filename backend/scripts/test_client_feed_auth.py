"""Live feed auth handshake test (2026-10-21). Runs the feed endpoint in-process with
Starlette's TestClient; nothing touches the running service.

    cd backend && python scripts/test_client_feed_auth.py [path/to/client_feed.py]
"""
import importlib.util
import json
import os
import sys
import time

sys.path.insert(0, os.getcwd())
from fastapi import FastAPI  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from jose import jwt  # noqa: E402

from app.config import settings  # noqa: E402
from app.services.auth_service import JWT_ALGORITHM  # noqa: E402

path = sys.argv[1] if len(sys.argv) > 1 else "app/ws/client_feed.py"
spec = importlib.util.spec_from_file_location("client_feed_under_test", path)
cf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cf)
cf.AUTH_WAIT_S = 1.0           # the real wait is 5 s; 1 s keeps the test quick

app = FastAPI()
app.websocket("/ws/feed")(cf.client_feed_endpoint)
client = TestClient(app)
good = jwt.encode({"sub": "feedtest-user", "exp": int(time.time()) + 300}, settings.JWT_SECRET, algorithm=JWT_ALGORITHM)
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


def user_ids():
    return [c.user_id for c in cf.client_manager._clients]


# 1. auth frame with a valid token -> authenticated, copy_updates allowed
with client.websocket_connect("/ws/feed") as ws:
    ws.send_text(json.dumps({"type": "auth", "token": good}))
    a = json.loads(ws.receive_text())
    ws.send_text(json.dumps({"subscribe": "copy_updates"}))
    b = json.loads(ws.receive_text())
    check("auth frame, valid token", a == {"authenticated": True} and b.get("subscribed") == "copy_updates", f"{a} {b}")
    check("auth frame user is registered", "feedtest-user" in user_ids())

# 2. guest frame -> public channels work, private refused
with client.websocket_connect("/ws/feed") as ws:
    ws.send_text(json.dumps({"type": "auth", "token": None}))
    a = json.loads(ws.receive_text())
    ws.send_text(json.dumps({"subscribe": "market_state"}))
    b = json.loads(ws.receive_text())
    ws.send_text(json.dumps({"subscribe": "copy_updates"}))
    c = json.loads(ws.receive_text())
    check("guest frame keeps public prices", a == {"authenticated": False} and b.get("subscribed") == "market_state", f"{a} {b}")
    check("guest frame refused private channel", "error" in c, str(c))

# 3. bad token in the frame -> closed 4001
try:
    with client.websocket_connect("/ws/feed") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": "eyJbad.token.value"}))
        ws.receive_text()
    check("bad token closed", False, "socket stayed open")
except WebSocketDisconnect as e:
    check("bad token closed with 4001", e.code == 4001, f"code {e.code}")

# 4. silence -> closed 4401 after the wait
t0 = time.time()
try:
    with client.websocket_connect("/ws/feed") as ws:
        ws.receive_text()
    check("silent socket closed", False, "socket stayed open")
except WebSocketDisconnect as e:
    check("silent socket closed with 4401 after the wait", e.code == 4401 and 0.8 < time.time() - t0 < 3, f"code {e.code}, {time.time() - t0:.1f}s")

# 5. legacy ?token= (tabs opened before the change) still works until 2026-10-28
with client.websocket_connect(f"/ws/feed?token={good}") as ws:
    ws.send_text(json.dumps({"subscribe": "copy_updates"}))
    b = json.loads(ws.receive_text())
    check("legacy ?token= path", b.get("subscribed") == "copy_updates", str(b))

# 6. legacy guest tab whose first frame is a subscribe -> served as guest
with client.websocket_connect("/ws/feed") as ws:
    ws.send_text(json.dumps({"subscribe": "market_state"}))
    b = json.loads(ws.receive_text())
    check("legacy guest subscribe-first", b.get("subscribed") == "market_state", str(b))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
