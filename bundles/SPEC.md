# Bundle spec

A **bundle** is a named, installable curated set of skills. Install it in one shot
instead of hand-listing skills:

```bash
npx github:onetest-ai/applied_skills init --bundle brain
./install.sh --bundle brain            # equivalent, from a checkout
```

(`--factory` is a back-compat alias for `--bundle`, matching the sdlc-skills installer.)

## Layout

```
bundles/<id>/
  factory.json               # the manifest (required)
  README.md                  # how the bundle works (mermaid diagrams encouraged)
  skills/<name>/             # the bundle's own skills (single source of truth)
  .claude-plugin/plugin.json # optional — makes the bundle a Claude Code plugin too
```

A bundle is **self-contained**: its skills live inside its own `bundles/<id>/skills/` dir,
so that dir is the single source of truth (no top-level `skills/`). The installer resolves
each name in the ordered `skills` list against `bundles/<id>/skills/<name>/` and copies (or
symlinks) it into every selected host's native `skills/` dir.

A bundle **may also be a Claude Code plugin**: add a `bundles/<id>/.claude-plugin/plugin.json`
and list it in the top-level `.claude-plugin/marketplace.json` (`source: ./bundles/<id>`).
Skills are auto-discovered from the sibling `skills/` default location — no `skills` path
field is needed in the plugin manifest.

## `factory.json`

| Field | Meaning |
|---|---|
| `id` | bundle id (must equal the dir name) |
| `title`, `description` | shown to the user |
| `skills` | **ordered** list of skill dir names from `bundles/<id>/skills/` — installed by default |
| `optionalSkills` | extra skills installed only with `--optional` |
| `entrypoint` | the skill a host should treat as the orchestrator |
| `targets` | hosts this bundle supports (`claude`, `dsh`, `copilot`, `codex`) |
| `store`, `deps` | informational — the store shape and runtime deps |

The installer validates that every name in `skills` / `optionalSkills` resolves to a real
`bundles/<id>/skills/<name>/` dir and errors out listing any that don't.

## Adding a bundle

1. `mkdir -p bundles/<id>/skills`
2. write `factory.json` (at least `id`, `title`, `skills`)
3. add the skill dirs under `bundles/<id>/skills/`
4. write `README.md`
5. (optional) add `bundles/<id>/.claude-plugin/plugin.json` and list it in the marketplace
6. `./install.sh --bundle <id> --dry-run` to verify resolution

## Bundles

- **brain** — local truthful knowledge engine (one `knowledge.sqlite` + Obsidian vault;
  meaning is agentic, numbers are computed). See [`brain/README.md`](brain/README.md).
