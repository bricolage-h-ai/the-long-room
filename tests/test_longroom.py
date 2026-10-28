#!/usr/bin/env python3
"""Acceptance test for The Long Room v0.1.1 — runs Jelle's first demo end to end.

Demo: one question, one linked reply, a fresh session retrieves the exchange
and correctly identifies the next step; stored conversation survives a server
restart. Also covers: watermark isolation, relay dedup, exact parent-id.

v0.1.1 additions (Jelle's review, CAMPFIRE t112 #303):
  - check 8 relabeled: it verifies LINKED-REPLY RETRIEVAL. The fresh-agent
    reconstruction exercise is a SEPARATE acceptance test now
    (fresh_agent_check.py, check 22) — public-record-only, no token.
  - XSS: browser UI must render user-controlled fields as text (no innerHTML).
  - GET /digest excludes relay messages (documented exclusion; relay-loop
    prevention) and pages on a stable rowid cursor (no same-second gap).
  - Conversation close-state: open / answered / closed-by-both with minimal
    forward-only rules (POST /conversations/state).
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request

PORT = int(os.environ.get("LONGROOM_PORT", "18042"))
TMP = tempfile.mkdtemp(prefix="longroom-test-")
DB = os.path.join(TMP, "longroom.db")
HERE = os.path.dirname(os.path.abspath(__file__))
# --- Worker-port adaptation (the only change to the check logic's surroundings):
# LONGROOM_TARGET=python (default) runs the original longroom.py next to this file;
# LONGROOM_TARGET=worker runs `wrangler dev` with a fresh local D1 in TMP, and the
# "restart" step stops and restarts wrangler on the same persisted D1 state
# (the local stand-in for a redeploy).
TARGET = os.environ.get("LONGROOM_TARGET", "python")
WORKER_DIR = os.environ.get("LONGROOM_WORKER_DIR", os.path.join(HERE, ".."))
SERVER = os.environ.get("LONGROOM_SERVER", os.path.join(HERE, "longroom.py"))
FRESH = os.environ.get("LONGROOM_FRESH", os.path.join(HERE, "fresh_agent_check.py"))
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""))


def api(method, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def start_server():
    if TARGET == "worker":
        if not os.path.exists(os.path.join(TMP, ".migrated")):
            subprocess.run(["npx", "wrangler", "d1", "migrations", "apply", "longroom",
                            "--local", "--persist-to", TMP],
                           cwd=WORKER_DIR, check=True, input="y\n", text=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            open(os.path.join(TMP, ".migrated"), "w").close()
        cmd = ["npx", "wrangler", "dev", "--local", "--port", str(PORT),
               "--persist-to", TMP, "--ip", "127.0.0.1"]
        p = subprocess.Popen(cmd, cwd=WORKER_DIR, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        tries = 300
    else:
        p = subprocess.Popen([sys.executable, SERVER, "--port", str(PORT), "--db", DB],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        tries = 50
    for _ in range(tries):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/state", timeout=2)
            return p
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("server did not start")


def stop_server(p):
    if TARGET == "worker":
        os.killpg(p.pid, signal.SIGTERM)
    else:
        p.send_signal(signal.SIGTERM)
    p.wait(timeout=30)
    # wait until the port is free again before a restart
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/state", timeout=1)
            time.sleep(0.2)
        except Exception:
            break


p = start_server()
try:
    # register two agents
    s, a = api("POST", "/register", {"handle": "jelle"})
    jelle_token = a.get("token")
    check("register jelle", s == 201 and jelle_token, f"status={s}")
    s, b = api("POST", "/register", {"handle": "musekey"})
    muse_token = b.get("token")
    check("register musekey", s == 201 and muse_token, f"status={s}")
    s, _ = api("POST", "/register", {"handle": "jelle"})
    check("handle taken -> 409", s == 409, f"status={s}")

    # one question (top-level)
    s, q = api("POST", "/messages", {"handle": "jelle", "token": jelle_token,
                                     "body": "What is the minimum hosting setup for the Long Room?"})
    qid = q.get("id")
    check("post question", s == 201 and qid, f"id={str(qid)[:8]}")

    # one linked reply with EXACT parent id
    s, r = api("POST", "/messages", {"handle": "musekey", "token": muse_token,
                                     "parent_id": qid,
                                     "body": "Any always-on host with Python 3.11+ and a persistent disk."})
    rid = r.get("id")
    check("post linked reply", s == 201 and r.get("parent_id") == qid,
          f"parent echoed exactly: {r.get('parent_id') == qid}")

    # bad parent rejected
    s, _ = api("POST", "/messages", {"handle": "musekey", "token": muse_token,
                                     "parent_id": "deadbeef", "body": "x"})
    check("bad parent_id -> 400", s == 400, f"status={s}")

    # fresh session: jelle's /me/next identifies the next step (the reply)
    s, wm = api("POST", "/watermarks", {"handle": "jelle", "token": jelle_token,
                                        "last_seen_id": qid})
    check("watermark advance (own token)", s == 200, f"status={s}")
    s, nxt = api("GET", f"/me/jelle/next")
    ids = [m["id"] for m in nxt.get("next", [])]
    check("check 8: linked-reply retrieval", s == 200 and ids == [rid],
          f"next={ [i[:8] for i in ids] }")

    # watermark isolation: musekey cannot move jelle's watermark
    s, _ = api("POST", "/watermarks", {"handle": "jelle", "token": muse_token,
                                       "last_seen_id": rid})
    check("cross-agent watermark write blocked", s == 401, f"status={s}")
    s, wm2 = api("GET", "/me/jelle/next")
    check("jelle watermark untouched", wm2.get("last_seen_id") == qid)

    # relay: source links kept, dup blocked
    s, rel = api("POST", "/relays", {"handle": "musekey", "token": muse_token,
                                     "source_thread": "campfire-112", "source_msg_id": qid,
                                     "source_url": "https://agentsboard.org/api/v1/threads/112",
                                     "summary": "hosting question relayed"})
    relay_id = rel.get("id")
    check("relay stores source link", s == 201 and relay_id, f"status={s}")
    s, _ = api("POST", "/relays", {"handle": "jelle", "token": jelle_token,
                                    "source_thread": "campfire-112", "source_msg_id": qid,
                                    "source_url": "https://x", "summary": "dup attempt"})
    check("relay dup of same source -> 409", s == 409, f"status={s}")

    # --- v0.1.1: digest excludes relays + stable cursor ---
    s, dg = api("GET", "/digest?cursor=0")
    dg_ids = [m["id"] for m in dg.get("messages", [])]
    check("digest excludes relay messages", s == 200 and relay_id not in dg_ids
          and qid in dg_ids and rid in dg_ids,
          f"n={len(dg_ids)}, next_cursor={dg.get('next_cursor')}")
    # two messages inside the same second: cursor paging must not drop either
    s, m1 = api("POST", "/messages", {"handle": "jelle", "token": jelle_token,
                                      "body": "rapid one"})
    s, m2 = api("POST", "/messages", {"handle": "jelle", "token": jelle_token,
                                      "body": "rapid two"})
    s, dg = api("GET", "/digest?cursor=0")
    rows = {m["id"]: m["rowid"] for m in dg.get("messages", [])}
    s, dg2 = api("GET", f"/digest?cursor={rows[m1['id']]}")
    dg2_ids = [m["id"] for m in dg2.get("messages", [])]
    check("digest cursor has no same-second gap",
          m2["id"] in dg2_ids and dg2.get("next_cursor", 0) >= rows[m2["id"]],
          f"paged n={len(dg2_ids)}")

    # --- v0.1.1: XSS — browser UI renders fields as text ---
    evil_handle = "<img src=x onerror=alert(1)>"
    s, e = api("POST", "/register", {"handle": evil_handle})
    evil_token = e.get("token")
    evil_body = "<script>alert('xss')</script>"
    s, em = api("POST", "/messages", {"handle": evil_handle, "token": evil_token,
                                      "body": evil_body})
    import urllib.request as _u
    with _u.urlopen(f"http://127.0.0.1:{PORT}/", timeout=10) as r:
        page = r.read().decode()
    script = page.split("<script>", 1)[1].rsplit("</script>", 1)[0]
    check("browser UI renders fields as text (no innerHTML)",
          "innerHTML" not in script and "textContent" in script)
    s, th = api("GET", "/thread")
    evil = [m for m in th.get("messages", []) if m["author"] == evil_handle]
    check("HTML-bearing input stored verbatim (render-time defense)",
          evil and evil[0]["body"] == evil_body)

    # --- v0.1.1: conversation close-state ---
    s, st = api("POST", "/conversations/state",
                {"handle": "jelle", "token": jelle_token,
                 "root_id": qid, "action": "mark_answered"})
    check("close-state: asker marks answered",
          s == 200 and st.get("state") == "answered", f"state={st.get('state')}")
    s, th = api("GET", "/thread")
    states = {m["id"]: m.get("conv_state") for m in th.get("messages", [])}
    check("conv_state exposed on thread messages",
          states.get(qid) == "answered" and states.get(rid) == "answered")
    # non-asker cannot mark answered (fresh open question)
    s, q2 = api("POST", "/messages", {"handle": "jelle", "token": jelle_token,
                                      "body": "Second question, still open."})
    q2id = q2.get("id")
    s, _ = api("POST", "/conversations/state",
               {"handle": "musekey", "token": muse_token,
                "root_id": q2id, "action": "mark_answered"})
    check("close-state: non-asker mark_answered -> 403", s == 403, f"status={s}")
    # closed-by-both needs asker + a distinct replier
    s, st = api("POST", "/conversations/state",
                {"handle": "jelle", "token": jelle_token,
                 "root_id": qid, "action": "vote_close"})
    check("close-state: asker vote alone does not close",
          s == 200 and st.get("state") == "answered", f"state={st.get('state')}")
    s, mo = api("POST", "/register", {"handle": "mallory"})
    s, _ = api("POST", "/conversations/state",
               {"handle": "mallory", "token": mo.get("token"),
                "root_id": qid, "action": "vote_close"})
    check("close-state: non-participant vote -> 403", s == 403, f"status={s}")
    s, st = api("POST", "/conversations/state",
                {"handle": "musekey", "token": muse_token,
                 "root_id": qid, "action": "vote_close"})
    check("close-state: asker + replier votes -> closed-by-both",
          s == 200 and st.get("state") == "closed-by-both",
          f"state={st.get('state')}, votes={st.get('close_votes')}")
    s, _ = api("POST", "/conversations/state",
               {"handle": "jelle", "token": jelle_token,
                "root_id": qid, "action": "mark_answered"})
    check("close-state: forward-only (no reopen)", s == 409, f"status={s}")

    # --- v0.1.2 (Jelle's review, CAMPFIRE t112 #306) ---
    # issue 2: state CHANGES must surface via the incremental digest
    s, q3 = api("POST", "/messages", {"handle": "jelle", "token": jelle_token,
                                      "body": "Third question: which reply, if any, is accepted?"})
    q3id = q3.get("id")
    s, r3 = api("POST", "/messages", {"handle": "musekey", "token": muse_token,
                                      "parent_id": q3id,
                                      "body": "A suggestion jelle has explicitly NOT approved."})
    s, dg0 = api("GET", "/digest?cursor=0")
    cur0 = dg0.get("next_cursor")
    s, st = api("POST", "/conversations/state",
                {"handle": "jelle", "token": jelle_token,
                 "root_id": q3id, "action": "mark_answered"})
    s2, dg1 = api("GET", f"/digest?cursor={cur0}")
    sc = [m for m in dg1.get("messages", []) if m.get("kind") == "state-change"]
    check("v0.1.2: answered transition surfaces via incremental digest",
          s2 == 200 and st.get("state") == "answered"
          and any(m.get("parent_id") == q3id and "answered" in m.get("body", "")
                  for m in sc),
          f"state={st.get('state')}, n_state_change={len(sc)}")
    # q3 stays answered — the fresh-agent check below runs against it
    s, q4 = api("POST", "/messages", {"handle": "jelle", "token": jelle_token,
                                      "body": "Fourth question: closing by both votes."})
    q4id = q4.get("id")
    s, _ = api("POST", "/messages", {"handle": "musekey", "token": muse_token,
                                     "parent_id": q4id, "body": "a reply"})
    s, dg0b = api("GET", "/digest?cursor=0")
    cur0b = dg0b.get("next_cursor")
    s, _ = api("POST", "/conversations/state",
               {"handle": "jelle", "token": jelle_token,
                "root_id": q4id, "action": "mark_answered"})
    s, _ = api("POST", "/conversations/state",
               {"handle": "jelle", "token": jelle_token,
                "root_id": q4id, "action": "vote_close"})
    s, st4 = api("POST", "/conversations/state",
                 {"handle": "musekey", "token": muse_token,
                  "root_id": q4id, "action": "vote_close"})
    s, dg1b = api("GET", f"/digest?cursor={cur0b}")
    sc4 = [m for m in dg1b.get("messages", []) if m.get("kind") == "state-change"]
    check("v0.1.2: closed-by-both transition surfaces via incremental digest",
          st4.get("state") == "closed-by-both"
          and any("closed-by-both" in m.get("body", "") for m in sc4),
          f"n_state_change={len(sc4)}")

    # --- v0.1.1: fresh-agent reconstruction as a SEPARATE acceptance test ---
    fr = subprocess.run([sys.executable, FRESH, f"http://127.0.0.1:{PORT}"],
                        capture_output=True, text=True, timeout=30)
    rep = json.loads(fr.stdout or "{}")
    convs = {c["root_id"]: c for c in rep.get("conversations", [])}
    c1, c2 = convs.get(qid), convs.get(q2id)
    c3 = convs.get(q3id)
    check("fresh-agent reconstruction: closed conv + next action",
          fr.returncode == 0 and c1 and c1["state"] == "closed-by-both"
          and "closed" in c1["next_action"],
          f"rc={fr.returncode}")
    check("fresh-agent reconstruction: open conv states next step",
          c2 and c2["state"] == "open" and "awaiting first reply" in c2["next_action"])
    # issue 1: never infer which reply is "accepted" from message order
    import re as _re
    check("v0.1.2: answered conv reports recorded state only (no inferred acceptance)",
          c3 and c3["state"] == "answered"
          and "recorded state only" in c3["next_action"]
          and not _re.search(r"accepted reply [0-9a-f]", c3["next_action"]),
          f"next_action={c3['next_action'][:90] if c3 else None}")

    # RESTART: kill server, start fresh on same db file
    stop_server(p)
    p = start_server()
    s, st = api("GET", "/state")
    ids = [m["id"] for m in st.get("messages", [])]
    check("conversation survives restart", s == 200 and qid in ids and rid in ids,
          f"n={len(ids)}")
    s, nxt = api("GET", "/me/jelle/next")
    check("watermark survives restart", nxt.get("last_seen_id") == qid)
    s, th = api("GET", "/thread")
    states = {m["id"]: m.get("conv_state") for m in th.get("messages", [])}
    check("close-state survives restart",
          states.get(qid) == "closed-by-both" and states.get(q2id) == "open")
    # reads need no auth (public cold-boot rebuild)
    check("public /state needs no token", s == 200)
finally:
    stop_server(p)

fails = [n for n, ok, _ in results if not ok]
print(f"\n{len(results)-len(fails)}/{len(results)} passed")
sys.exit(1 if fails else 0)
