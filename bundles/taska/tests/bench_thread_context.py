"""Benchmark: match_task score WITH vs WITHOUT thread context.

Run with:
  uv run --with pytest --with pyyaml python bundles/taska/tests/bench_thread_context.py

Outputs a table showing score delta for each scenario so we can measure
exactly how much thread context improves matching — and which message
classes benefit most.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../skills/run"))

from taska_lib.sources.teams_channel import normalize_message, match_task, enrich_thread_context

# ── invented test corpus (no real project data) ──────────────────────────────
# Each scenario: (scenario_name, parent_body, reply_body, reply_attachment, expected_task_id)
SCENARIOS = [
    (
        "vague reply + parent has keywords",
        "Can someone share the updated budget tracker when it's ready?",
        "Yes, here it is.",
        "budget-tracker-v2.xlsx",
        "T_BUDGET",
    ),
    (
        "short approval with file",
        "We agreed to move forward with the risk register — please upload the final version.",
        "Done, uploaded.",
        "risk-register-final.xlsx",
        "T_RISK",
    ),
    (
        "pronoun reply — 'that' refers to parent",
        "Does anyone have the onboarding checklist ready to share with new joiners?",
        "Here's that document.",
        "onboarding-checklist.docx",
        "T_ONBOARD",
    ),
    (
        "standalone good message (control — should pass even without parent)",
        "",   # no parent
        "Sharing the project charter for final review before sign-off.",
        "project-charter-v3.docx",
        "T_CHARTER",
    ),
    (
        "generic filename + vague body (hardest case)",
        "Please upload the deliverable tracking sheet before EOD.",
        "Done.",
        "document-1.xlsx",
        "T_TRACKER",
    ),
]

OPEN_TASKS = [
    {"id": "T_BUDGET",   "title": "Update and share budget tracker"},
    {"id": "T_RISK",     "title": "Finalise risk register"},
    {"id": "T_ONBOARD",  "title": "Prepare onboarding checklist for new joiners"},
    {"id": "T_CHARTER",  "title": "Draft and circulate project charter"},
    {"id": "T_TRACKER",  "title": "Complete deliverable tracking sheet"},
]

THRESHOLD = 0.4

def _make_raw(msg_id, reply_to_id, body_html, attachment_name, ts="2026-10-22T10:00:00.000Z"):
    att = [{"id": "x1", "name": attachment_name,
             "contentUrl": f"https://example.com/{attachment_name}"}] if attachment_name else []
    return {
        "id": msg_id, "replyToId": reply_to_id,
        "createdDateTime": ts,
        "from": {"user": {"displayName": "Alex"}},
        "body": {"contentType": "html", "content": f"<p>{body_html}</p>"},
        "attachments": att,
    }


def _score(msg):
    """Return (task_id_matched, score) regardless of threshold."""
    result = match_task(msg, OPEN_TASKS, threshold=0.0)
    return (result["task_id"], result["score"]) if result else ("—", 0.0)


def run():
    COL = [46, 10, 10, 8, 8]
    header = f"{'Scenario':<{COL[0]}} {'Without':>{COL[1]}} {'With':>{COL[2]}} {'Delta':>{COL[3]}} {'Pass?':>{COL[4]}}"
    print("\n" + "=" * sum(COL) + "=")
    print(header)
    print("-" * (sum(COL) + 1))

    wins = ties = regressions = 0
    for name, parent_body, reply_body, attachment, expected_id in SCENARIOS:
        # WITHOUT thread context: normalize reply in isolation (context_text stays "")
        reply_raw  = _make_raw("reply1", None, reply_body, attachment)
        msg_alone  = normalize_message(reply_raw)
        _, score_alone = _score(msg_alone)

        # WITH thread context: simulate parent + reply batch → enrich_thread_context
        parent_raw = _make_raw("parent1", None, parent_body, None, "2026-10-22T09:00:00.000Z")
        reply_raw2 = _make_raw("reply1", "parent1", reply_body, attachment, "2026-10-22T10:00:00.000Z")
        batch = [normalize_message(parent_raw), normalize_message(reply_raw2)]
        enriched = enrich_thread_context(batch)
        reply_msg  = next(m for m in enriched if m["message_id"] == "reply1")
        _, score_ctx = _score(reply_msg)

        delta  = score_ctx - score_alone
        passes = "✓" if score_ctx >= THRESHOLD else "✗"
        missed = " ←FIXED" if score_alone < THRESHOLD and score_ctx >= THRESHOLD else ""
        broken = " ←BROKE" if score_alone >= THRESHOLD and score_ctx < THRESHOLD else ""

        if delta > 0.005:
            wins += 1
        elif delta < -0.005:
            regressions += 1
        else:
            ties += 1

        print(f"{name:<{COL[0]}} {score_alone:>{COL[1]}.3f} {score_ctx:>{COL[2]}.3f} "
              f"{delta:>+{COL[3]}.3f} {passes:>{COL[4]}}{missed}{broken}")

    print("=" * (sum(COL) + 1))
    print(f"Improvements: {wins}  Ties: {ties}  Regressions: {regressions}")
    print(f"Threshold: {THRESHOLD}\n")


if __name__ == "__main__":
    run()
