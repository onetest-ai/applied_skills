# Contributing to applied-skills

Thanks for your interest in improving **applied-skills** — a toolkit of generic,
corpus-agnostic agent skills for building and querying a local, truthful Brain
over documents, spreadsheets, and knowledge graphs.

## Ground rules

- **No project-specific data, paths, or credentials.** This repo ships *generic*
  skills. Anything tied to a particular corpus, client, or engagement stays in the
  consuming project — never here. PRs that add such data will be asked to remove it.
- **Keep skills self-contained.** Each skill is code + docs under its own directory;
  it should not reach into another skill's internals.
- **Torch-free.** The RAG lane uses `fastembed`/onnx. Don't add heavyweight ML
  dependencies without discussion.

## Getting set up

```bash
git clone https://github.com/onetest-ai/applied_skills.git
cd applied_skills
# Python deps for the brain bundle (isolated venv via uv):
./install.sh --bundle brain --deps        # builds .claude/venv
# or zero-install:  uv run --with-requirements bundles/brain/requirements.txt python <script>
```

## Running the tests

The Python test suites live under each bundle:

```bash
uv run --with-requirements bundles/brain/requirements.txt --with pytest python -m pytest bundles/brain/tests
uv run --with pytest python -m pytest bundles/kb/tests
```

Please make sure the relevant suite is green before opening a PR.

## Making a change

1. **Open an issue first** for anything non-trivial (see the templates when you
   click *New issue*) so we can agree on the approach.
2. Branch from `main`: `git checkout -b feat/short-description` (or `fix/…`, `docs/…`).
3. Keep the change focused; match the style and comment density of the surrounding
   code.
4. Add or update tests for behavior changes.
5. Update the relevant `SKILL.md` / `README.md` when you change how a skill is used.

## Opening a pull request

- Fill in the pull-request template.
- Reference the issue it closes (`Closes #123`).
- Confirm no secrets, client data, or machine-specific paths are included.
- Expect review focused on correctness, reuse/simplification, and whether the skill
  stays generic.

## Reporting bugs and requesting features

Use the issue templates:

- **Bug report** — what you ran, what happened, what you expected, and how to reproduce.
- **Feature request** — the problem you're trying to solve, not just a proposed API.

For anything security-sensitive, **do not open a public issue** — see
[SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE).
