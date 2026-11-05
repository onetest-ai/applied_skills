"""People matters: staffing, allocation, extensions, roll-offs, rates, overtime, utilisation, a person's performance.

These are never tracked or reported. `people_matter` is the deterministic backstop behind the extractor's rule: it
must catch the way a manager says it, and it must leave ordinary work alone ("Adoption is at 60%", "Extend Checkout
by two weeks", "the allocation of orders to routes"), because a false hit hides real work from a client report.
A person is a known name the caller passes (full name or first name, possessive included) or him/her/them; a
capitalised word is never taken for one. Wording about people (allocated, released, extended, a percentage, full
time, rolled off, performance) fires only next to a person. Overtime, utilisation, rate cards and hourly or day
rates fire alone, as does "our staffing plan/changes". A few nameless phrases fire alone, only when the clause is itself a staffing
statement: "no new hires", "drawn from existing people/staff/team", and bringing, adding or hiring additional/extra
people, staff or headcount when the clause ends there or names the project, team, pilot or engagement ("bring additional
people to the workshop" stays work). Person-bound wording must sit in one clause with its staffing object: a
percentage of time, an end date, or a position held until a date, or a duration ("keep Ravi another six weeks"). A call-centre staffing forecast stays work."""
import re
from collections.abc import Iterable
from functools import lru_cache

from . import store as S

_WORD = r"[\w'’%-]+"
_GAP = rf"(?:{_WORD}[ \t,]+){{0,4}}"
_PCT = r"\d+ ?%"
# a percentage is someone's time only when it closes the clause or is followed by a time word or an end/date word
_TIMEWORD = (r"(?=\s*(?:[,.;!?)]|$)|\s+(?:of (?:her|his|their|my|the) time|fte|capacity|allocat\w*|time"
             r"|(?:until|till|to the end|through|from|for|by|next|this|starting|effective|and|then)\b))")
_PCTT = _PCT + _TIMEWORD
_POSS = r"(?:['’]s)?"


def _person(names: tuple[str, ...]) -> str:
    forms: set[str] = set()
    for name in names:
        parts = name.split()
        if parts:
            forms.add(r"\s+".join(map(re.escape, parts)))
            forms.add(re.escape(parts[0]))
    alts = sorted(forms, key=len, reverse=True) + ["him", "her", "them"]
    return rf"\b(?:{'|'.join(alts)}){_POSS}\b"


@lru_cache(maxsize=64)
def _build(names: tuple[str, ...]) -> re.Pattern[str]:
    p = _person(names)
    sent = r"[^.!?\n]*"              # the rest of the sentence
    after = rf"{p}[ \t,]+{_GAP}"      # a person, then up to four words, then the wording
    return re.compile(
        r"(?:"
        r"\bovertime\b|\butili[sz]ation\b|\brate[- ]card\b"
        r"|\b(?:hourly|daily|day|billing|billable|blended)[- ]rates?\b"
        r"|\b(?:our|the team's|team) staffing (?:plan|changes?)\b"
        rf"|{after}(?:is |are |was |will be )?(?:\w+ )?allocat\w*"
        rf"|\ballocat\w*\s+(?:(?:of|for|to)\s+)?{p}"
        rf"|\breleas(?:e|es|ed|ing)\s+{p}"
        rf"|{p}[ \t]+(?:is|are|was|will be|gets|to be)\s+releas\w*"
        rf"|\bextend\w*\s+{p}"
        rf"|{after}\bextensions?\b|\bextensions?\s+(?:for|of)\s+{p}"
        rf"|{p}[ \t,]+(?:{_WORD}[ \t,]+){{0,2}}(?:at|to|on)\s+{_PCTT}"
        rf"|{after}\b(?:half|part|full)[- ]time\b"
        rf"|{after}\broll(?:s|ed|ing)?[- ]off\b|\broll(?:s|ed|ing)?\s+{p}\s+off\b"
        rf"|{after}\bramp(?:s|ed|ing)?[- ](?:up|down)\b"
        r"|\b(?:he|she|they)\s+(?:\w+\s+){0,2}ramp(?:s|ed|ing)?[- ](?:up|down)\s+(?:\w+\s+)?to\s+\d+ ?%"
        rf"|{p}\s+rate\b"
        rf"|{after}(?:not delivering|under-?perform\w*|struggling|not pulling)"
        rf"|{p}\s+performance\b|\bperformance (?:review|issue|problem)s?\s+(?:for|with|of)\s+{p}"
        rf"|\bstaffing\b[ \t,]+{_GAP}{p}|{after}\bstaffing\b|\bre-?staff\w*[ \t]+{_GAP}{p}"
        rf"|\bstaffed\s+(?:at|by|with)\s+(?:{_PCTT}|{p})"
        rf"|{p}[ \t,]+stay\w*\b[ \t,]+(?:{_WORD}[ \t,]+){{0,4}}at\s+{_PCTT}"
        rf"|{p}[ \t,]+(?:keeps?|kept|stays?|at)\s*\(\s*{_PCT}\s*\){_TIMEWORD}"
        rf"|{p}[ \t,]+remove(?=\s*[,.!?;]|\s*$|\s+from\s+the\s+(?:project|team|engagement)\b|[ \t,]+(?:\w+[ \t]+){{0,2}}not (?:going to )?extend)"
        rf"|{p}[ \t]+(?:(?:is|was|gets|got) )?releas(?:ed|es)\s+(?:at the end\b|from the (?:project|team|engagement)\b|as of\b|effective\b)"
        rf"|{p}[ \t,]+(?:{_WORD}[ \t,]+){{0,6}}?extend\w*\s+(?:for now\s+)?(?:till|until)\s+(?:the\s+)?(?:end|mid)\b"
        rf"|{p}\s+staffed\s+on\s+(?:the\s+)?(?:[\w-]+\s+){{0,3}}(?:position|project|team|workstream)\b"
        rf"|{p}\s+on\s+(?:the\s+)?(?:[\w-]+\s+){{0,2}}position\s+(?:until|till|through)\s+(?:the\s+)?(?:mid|end|\w+ week)\b"
        rf"|\bkeep\w*\s+{p}[ \t,]+(?:{_WORD}[ \t,]+){{0,3}}?(?:another(?: \w+)?|\w+ more)\s+(?:days?|weeks?|months?|sprints?)\b"
        r"|\bno new hires\b|\bdrawn from (?:the )?existing (?:people|staff|team)\b"
        r"|\b(?:bring|add|hire|need)\w*\s+(?:in\s+)?(?:\w+\s+){0,2}(?:additional|extra|more) (?:people|staff|headcount)"
        r"(?=\s*[,.!?;]|\s*$|\s+(?:for|onto|on|to)\s+the\s+(?:project|team|pilot|engagement)\b)"
        r")",
        re.I,
    )


PEOPLE_MATTERS = _build(())


def people_matter(*texts: str | None, names: Iterable[str] = ()) -> bool:
    """True when any of the texts speaks about a person's staffing, allocation, pay or performance."""
    pattern = _build(tuple(sorted({n.strip() for n in names if n.strip()})))
    return any(t and pattern.search(t) for t in texts)


def names(cfg) -> list[str]:
    """Every name and alias of the project's people: who `people_matter` takes for a person."""
    return [x for p in cfg.people for x in (p.name, *p.aliases)]


def scrub_quotes(value: object, known: tuple[str, ...]) -> object:
    """`value` with every "quote" that is a people matter blanked, at any depth: the node and its title stay."""
    if isinstance(value, dict):
        return {k: "" if k == "quote" and isinstance(v, str) and people_matter(v, names=known) else scrub_quotes(v, known)
                for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_quotes(v, known) for v in value]
    return value


def hidden_ids(cfg, N: dict, store=None) -> set[str]:
    """Ids of nodes whose title or creating quote is a people matter: they may predate the guard in the ledger, and
    neither the client report nor the client deck data shows them. The one definition both read; `store` defaults to
    the project's own."""
    known = names(cfg)
    quote = {e.get("id"): e.get("quote") for e in S.events(cfg.store if store is None else store) if e.get("type") == "create"}
    return {i for i, n in N.items() if people_matter(n.get("title"), quote.get(i), names=known)}
