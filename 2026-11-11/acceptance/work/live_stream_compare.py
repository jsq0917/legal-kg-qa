"""HTTP 层实测：/ask/stream 与 /ask 的最终语义对比，并记录流式连接异常。"""

import http.client
import json
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8010"
QUERY = "试用期最长能约定多久？"
params = {"query": QUERY, "mode": "hybrid", "tier": "A_gates", "llm": "false"}

for _ in range(90):
    try:
        urllib.request.urlopen(BASE + "/health", timeout=5).read()
        break
    except Exception:
        time.sleep(2)

url = BASE + "/ask/stream?" + urllib.parse.urlencode(params)
events = []
err = None
t0 = time.time()
ev_name = None
try:
    with urllib.request.urlopen(url, timeout=180) as r:
        status = r.status
        while True:
            line = r.readline()
            if not line:
                break
            s = line.decode("utf-8").strip()
            if s.startswith("event: "):
                ev_name = s[7:]
            elif s.startswith("data: "):
                payload = json.loads(s[6:])
                payload["_event"] = ev_name
                events.append(payload)
except http.client.IncompleteRead as e:
    err = f"IncompleteRead(partial={len(e.partial)}B)"
except Exception as e:  # noqa: BLE001
    err = f"{type(e).__name__}: {e}"
stream_ms = round((time.time() - t0) * 1000)

body = json.dumps({"query": QUERY, "mode": "hybrid", "tier": "A_gates", "llm": False}).encode()
req = urllib.request.Request(BASE + "/ask", data=body, headers={"Content-Type": "application/json"})
t1 = time.time()
sync = json.loads(urllib.request.urlopen(req, timeout=180).read())
sync_ms = round((time.time() - t1) * 1000)

print("event_keys", [sorted(e) for e in events[:2]], "types", [e.get("_event") for e in events])
final = next((e for e in events if e.get("_event") == "final"), None)
done = next((e for e in events if e.get("_event") == "done"), None)
print("stream_status", status if "status" in dir() else None, "stream_error", err, "stream_ms", stream_ms,
      "events", [e.get("_event") for e in events])
print("sync_ms", sync_ms, "sync_cache", sync.get("cache"), "stream_done_cache", done and done.get("cache"))
print("final==sync answer", final and final.get("answer") == sync.get("answer"),
      "citations", final and final.get("citations") == sync.get("citations"),
      "refused", final and final.get("refused") == sync.get("refused"),
      "version", final and final.get("answer_version"), sync.get("answer_version"))
print("checks_count", len(final.get("checks", [])) if final else None,
      "sync_checks", len(sync.get("checks", [])))
print("graph_build_id", final and final.get("graph_build_id"), sync.get("graph_build_id"))
print("done", done)
