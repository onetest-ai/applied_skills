"""Source adapters. Each exposes `sync(cfg, key, scfg, store, cursor) -> (new_cursor, report)` and writes evidence
items through `store.write_evidence`. A cursor only advances past an item once its evidence is written, so a
failed transcription or an offline folder is retried on the next sync."""
import re

from . import code_host, files_folder, meetings_folder, msgraph

ADAPTERS = {
    "meetings_folder": meetings_folder.sync,
    "msgraph_transcripts": msgraph.sync_transcripts,
    "msgraph_calendar": msgraph.sync_calendar,
    "files_folder": files_folder.sync,
    "code_host": code_host.sync,
}


def meeting_kind(title, scfg):
    pats = scfg.get("client_patterns") or []
    if any(re.search(p, title or "", re.I) for p in pats):
        return "client"
    return scfg.get("kind", "internal")
