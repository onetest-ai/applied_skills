"""TDD for the shared month-name date helper `text.month_day` — the DRY target behind both
`text.resolve_due` (spoken due dates) and `meetings_folder.meeting_time` (recording file names),
which carried near-duplicate month regexes that had drifted apart. `month_day` returns the
(month, day) it finds, or None; the *year* is each caller's own concern (resolve_due resolves it
against the date spoken, meeting_time against the file mtime). Invented data only.
"""
import pytest

from taska_lib.text import month_day


@pytest.mark.parametrize("s,want", [
    ("March 14", (3, 14)),                               # month then day (resolve_due)
    ("Jan 5", (1, 5)),
    ("14th of October", (10, 14)),                       # day + ordinal + "of" (resolve_due docstring)
    ("August 25th", (8, 25)),                            # month then day with ordinal
    ("25 Aug", (8, 25)),                                 # day then month, spaced
    ("25_Aug", (8, 25)),                                 # underscore export name (meeting_time)
    ("06-Aug", (8, 6)),                                  # hyphen export name
    ("25_Aug_September Release Go-No-Go", (8, 25)),      # the real date wins over the release word "September"
    ("no date in here", None),
    ("version 3 of the plan", None),                     # a bare number is not a date
])
def test_month_day(s, want):
    assert month_day(s) == want
