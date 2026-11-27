# consult

A Claude Code plugin for guided consulting research, from onboarding to a reviewed deck: hypotheses, coached comparison research, customer-test synthesis, contact-data findings and rule-gated decks.

Nothing client-specific lives here. Rules the consultant teaches are kept in a client-free register (`rules/register.json`) and every skill, agent or check names the rule it enforces.

## Skills

- `lessons` (phase 1): mines past Claude Code sessions for the rules the consultant taught, keeps the register, and renders the lessons page.

## Tests

```bash
uv run --with pytest python -m pytest bundles/consult/tests
```

Stdlib only; no venv.
