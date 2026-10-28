#!/usr/bin/env python3
"""Fresh-agent reconstruction check — Jelle's acceptance test B (separate).

Reads ONLY public endpoints (no token) — exactly what a fresh agent session
sees on first contact. Reconstructs each top-level conversation from the
public record: ordered thread, conv_state, and the next action derivable
without any private context. Prints JSON to stdout.

This is the mechanical form of Mesh's continuity test: a stranger must be
able to say, from the public record alone, whether a conversation is open
or closed and what the agreed next action is.

v0.1.2 rule (Jelle's review, t112 #306): report RECORDED STATE ONLY. For an
'answered' conversation the public record names NO accepted reply — which
reply (if any) the asker accepted is not derivable from message order, so
the check never says "accepted reply <id>". Mechanical reconstruction
helper, not a judge.
"""
import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18042"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read())


def main():
    thread = get("/thread")["messages"]
    by_id = {m["id"]: m for m in thread}
    children = {}
    for m in thread:
        if m.get("parent_id"):
            children.setdefault(m["parent_id"], []).append(m["id"])

    def subtree(root_id):
        out, stack = [], [root_id]
        while stack:
            mid = stack.pop()
            if mid == root_id or mid in out:
                if mid != root_id:
                    continue
            if mid != root_id:
                out.append(mid)
            for c in children.get(mid, []):
                if c not in out:
                    stack.append(c)
        # order by appearance in /thread (rowid order)
        order = {m["id"]: i for i, m in enumerate(thread)}
        return sorted(out, key=lambda i: order.get(i, 0))

    convs = []
    for root in [m for m in thread
                 if not m.get("parent_id") and m.get("kind") == "post"]:
        reps = subtree(root["id"])
        state = root.get("conv_state", "open")
        # human posts only — state-change rows are mechanical bookkeeping,
        # relays are terminal annotations; neither is a conversational reply.
        human_reps = [i for i in reps if by_id[i].get("kind") == "post"]
        if state == "closed-by-both":
            nxt = "none — conversation closed by both sides"
        elif state == "answered":
            # Recorded state ONLY (v0.1.2): the public record names no
            # accepted reply, so acceptance is never inferred from order.
            nxt = ("answered — recorded state only; the public record names "
                   "no accepted reply; next: asker (%s) decides — not "
                   "derivable from the public record" % root["author"])
        elif not human_reps:
            nxt = "awaiting first reply"
        else:
            last = by_id[human_reps[-1]]
            nxt = ("open — latest reply by %s (%s): %.60s; next: asker reviews, "
                   "marks answered when satisfied" % (last["author"], last["id"][:8], last["body"]))
        convs.append({
            "root_id": root["id"],
            "root_id8": root["id"][:8],
            "question": root["body"][:80],
            "state": state,
            "reply_count": len(reps),
            "next_action": nxt,
        })
    print(json.dumps({"conversations": convs}, ensure_ascii=False))


if __name__ == "__main__":
    main()
