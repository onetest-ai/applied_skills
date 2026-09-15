#!/usr/bin/env python3
"""Advisory detectors for human review of auto-induced taxonomies.

Deterministic, stdlib-only (difflib + re), pure functions — no I/O, no
mutation of the taxonomy. These exist because auto-induced taxonomies
reliably need heavy human consolidation: near-duplicate labels
("Billing & Payments" / "Billing & Payments Admin" / "Billing & Payments
Administration") and off-axis L1s that are really roadmap phases
("Transform") or bare entities ("Costco") rather than intents.

Doctrine: flag, never fix. Nothing here deletes, merges, or rewrites the
taxonomy — that stays a human-gated, additive decision. Callers should
surface these as review annotations alongside the emitted taxonomy, not
apply them automatically.
"""
import re
from difflib import SequenceMatcher

# ---- near_duplicate_labels ----

def _norm(s):
    s = (s or "").lower().strip()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _similar(a, b, threshold):
    """True if normalized a/b look like the same label.

    Combines SequenceMatcher ratio with a prefix/substring check: one label
    fully containing another (e.g. "Billing & Payments" inside "Billing &
    Payments Administration") is a strong duplicate signal even when the
    length difference pulls the raw ratio down.
    """
    if not a or not b:
        return False
    if a == b:
        return True
    if a.startswith(b) or b.startswith(a) or a in b or b in a:
        return True
    return SequenceMatcher(None, a, b).ratio() >= threshold


def near_duplicate_labels(labels, threshold=0.86):
    """Group labels that look like near-duplicates of one another.

    Returns a list of groups; each group is a list of >=2 original labels
    (order preserved) whose normalized forms are mutually similar per
    `_similar`. Labels with no near-duplicate are omitted entirely (a
    solo group is not "flagged"). Advisory only — does not decide which
    variant is canonical.
    """
    # union-find over indices
    n = len(labels)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    normed = [_norm(l) for l in labels]
    for i in range(n):
        for j in range(i + 1, n):
            if _similar(normed[i], normed[j], threshold):
                union(i, j)

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(labels[i])

    return [g for g in groups.values() if len(g) > 1]


# ---- off_axis_l1 ----

_PHASE_WORDS = (
    "transform", "roadmap", "phase", "vision", "blueprint", "north star",
    "future state", "current state", "milestone", "wave",
)

# Words that signal the label is actually an intent/domain, not a bare
# entity — presence of any of these means we should NOT flag it.
_INTENT_CUES = (
    "&", "and", "management", "support", "billing", "payment", "payments",
    "onboarding", "renewal", "cancellation", "outage", "access", "request",
    "issue", "issues", "inquiry", "inquiries", "dispute", "disputes",
    "escalation", "provisioning", "reporting", "compliance", "security",
    "fraud", "refund", "refunds", "upgrade", "downgrade", "migration",
    "integration", "service", "services", "account", "accounts", "order",
    "orders", "shipping", "delivery", "technical", "sales", "marketing",
    "hr", "finance", "operations", "admin", "administration", "care",
    "help", "assistance", "inquiry",
)


def off_axis_l1(label):
    """Heuristic advisory: does this L1 label look off-axis for an intent
    taxonomy (a roadmap/phase label, or a bare entity/proper noun)?

    Returns a human-readable reason string when flagged, else None.

    This is tuned for recall, not precision: false positives are expected
    (e.g. a legitimate domain that happens to be a single word may get
    flagged). It is a prompt for a human to look, never a gate — nothing
    consumes this to auto-remove or auto-demote a label.
    """
    if not label or not label.strip():
        return None
    text = label.strip()
    lower = text.lower()

    for w in _PHASE_WORDS:
        if w in lower:
            return f"looks like a roadmap/phase label (matched {w!r}), not an intent"

    has_intent_cue = any(cue in lower for cue in _INTENT_CUES)
    if has_intent_cue:
        return None

    words = text.split()
    if len(words) == 1:
        return "single-token label — check it isn't a bare entity (company/product name) rather than an intent"

    if len(words) == 2 and all(w[:1].isupper() for w in words if w):
        # e.g. "Costco Wholesale", "Acme Corp" — two capitalized words with
        # no intent/domain cue read as a proper noun, not an intent class.
        return "two capitalized words with no intent/domain cue — check it isn't a bare proper-noun entity"

    return None
