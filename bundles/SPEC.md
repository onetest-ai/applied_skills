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
  factory.json     # the manifest (required)
  README.md        # how the bundle works (mermaid diagrams encouraged)
```

Skills themselves are **not** copied into the bundle dir — the manifest references them
by name from the repo's top-level `skills/`, so there is a single source of truth and no
duplicated code. The installer resolves the ordered `skills` list and copies (or symlinks)
each named skill into every selected host's native `skills/` dir.

## `factory.json`

| Field | Meaning |
|---|---|
| `id` | bundle id (must equal the dir name) |
| `title`, `description` | shown to the user |
| `skills` | **ordered** list of skill dir names from `skills/` — installed by default |
| `optionalSkills` | extra skills installed only with `--optional` |
| `entrypoint` | the skill a host should treat as the orchestrator |
| `targets` | hosts this bundle supports (`claude`, `dsh`, `copilot`, `codex`) |
| `store`, `deps` | informational — the store shape and runtime deps |

The installer validates that every name in `skills` / `optionalSkills` resolves to a real
`skills/<name>/` dir and errors out listing any that don't.

## Adding a bundle

1. `mkdir bundles/<id>`
2. write `factory.json` (at least `id`, `title`, `skills`)
3. write `README.md`
4. `./install.sh --bundle <id> --dry-run` to verify resolution

## Bundles

- **brain** — local truthful knowledge engine (one `knowledge.sqlite` + Obsidian vault;
  meaning is agentic, numbers are computed). See [`brain/README.md`](brain/README.md).
