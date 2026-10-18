# -*- coding: utf-8 -*-
"""C3 生产冒烟：两轮真实对话验证 A1(不截断)/A2(options持续)/A3(引号归一)。"""
import json
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:2143"
ENV_FILE = "/opt/fieldwork-agent/deploy/aliyun/fieldwork-web.env"
OWNER = "smoke-c3-owner"
PID = "field_smoke_c3"

token = ""
with open(ENV_FILE, encoding="utf-8") as f:
    for line in f:
        if line.startswith("FIELDWORK_APP_TOKEN="):
            token = line.split("=", 1)[1].strip()
assert token, "no FIELDWORK_APP_TOKEN in env"
HDRS = {"X-App-Token": token, "X-Owner-Token": OWNER,
        "Content-Type": "application/json"}


def call(method, path, body=None, timeout=15):
    req = urllib.request.Request(BASE + path, method=method, headers=HDRS,
                                 data=json.dumps(body).encode() if body else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.strip() else "")
    except urllib.error.HTTPError as e:  # 404（幂等清理）/409 等不炸
        return e.code, e.read().decode()[:200]


# 1) 建项目
st, r = call("DELETE", "/projects/" + PID)  # 幂等清理
st, r = call("POST", "/projects", {"project_id": PID, "discipline": "民族学",
                                   "topic": "鹤庆银器工艺", "purpose": "硕士论文田野"})
print("create:", st, r)

MSGS = [
    "我想研究云南鹤庆新华村的银器锻制工艺，当地被称为\"小锤敲过一千年\"。" \
    "请直接给我两个可选的研究切入点方向，并说明各自适合什么样的研究问题。",
    "选第二个方向。请展开讲讲它能回答什么研究问题，最后给我三个具体可选的子问题切入选项。",
]

for rnd, text in enumerate(MSGS, 1):
    st, msgs0 = call("GET", "/projects/%s/messages" % PID, timeout=20)
    prev_len = len(msgs0)  # 本轮前消息数：新 assistant 必须严格多于它，防误判上轮尾条
    st, r = call("POST", "/projects/%s/messages" % PID, {"text": text})
    tid = r["turn_id"]
    print("round %d turn: %s %s (prev_msgs=%d)" % (rnd, st, tid, prev_len))
    done = False
    t0 = time.time()
    while time.time() - t0 < 150:
        st, msgs = call("GET", "/projects/%s/messages" % PID, timeout=20)
        last = msgs[-1] if msgs else {}
        if len(msgs) > prev_len and last.get("role") == "assistant":
            # assistant 消息轮末一次性落库，出现即本轮完成（A1：完整才落）
            done = True
            content = last["content"]
            print("--- round %d len=%d tail: ...%s" % (
                rnd, len(content), content[-160:].replace("\n", " ")))
            print("    options_tag=%s quotes_fullwidth=%s halfquote_in_reply=%s quick=%s" % (
                "[[options]]" in content,
                ("\u201c" in content and "\"" not in content),
                "\"" in content,
                last.get("quick_replies")))
            break
        time.sleep(4)
    if not done:
        print("round %d TIMEOUT" % rnd)

st, _ = call("DELETE", "/projects/" + PID)
print("cleanup:", st)
print("SMOKE_DONE")
