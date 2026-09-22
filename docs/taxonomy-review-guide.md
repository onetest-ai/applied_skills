# Reviewing a Brain's taxonomy

This guide is for the person who maintains a Brain: the one who decides what its categories are. It covers the local review app that every taxonomy decision goes through.

## What the taxonomy is, and why you review it

A Brain's taxonomy is its list of categories: top-level categories (L1) with sub-categories (L2) under them, plus the metric inventory. Every section of every document is tagged with the categories it is about, and kb uses those tags, the category descriptions and the graph between them to answer "what is this about" and "how do these relate" questions.

Agents draft the taxonomy and propose fixes, but they only ever **add**: a new category, a description, a tag, a draft metric definition. Renaming, merging, moving, splitting and removing categories are your decisions. When you rename or merge a category in the app, the sections tagged with it move with it; nothing tagged is silently lost.

## Starting a review

You don't run the app yourself. Ask Claude in Claude Code, in the Brain project:

| Ask for | You get |
|---|---|
| (nothing: the first build asks you, once the corpus is classified) | the **first-build review**: real problems found in how the drafted categories actually fit the tagged sections |
| "review the taxonomy", "check / clean up the taxonomy" | the **health review**: every problem Claude found, each with a proposed fix |
| "propose new categories for the untagged sections" | a **refine review**: only proposals for new categories |
| "open the taxonomy editor", "I want to change some categories" | **browse**: the whole taxonomy and metric inventory, no proposals, for your own edits |

Claude prepares the review (for a health review, low-cost agents first draft a fix for each problem), then starts the app and tells you a browser tab is open. It then waits for you.

**How the app runs:**

- It is a small web server on your own machine, at `http://127.0.0.1:<port>/…`. It needs no network access and no extra install, and it reads the Brain without changing it.
- It opens a browser tab by itself. If no tab opened, ask Claude for the review URL and paste it into your browser. The URL carries a token that is new every time the app starts, so use the one Claude gives you.
- It closes when you click **Submit**, when you choose **Close the review without submitting**, or after an hour. Your decisions are saved as you make them, so if it closed before you were done, ask Claude to reopen the review and carry on.

## The first-build review

The very first review is not a review of the bare list of drafted categories — Claude classifies every section against the draft first, so the review reflects how the categories actually fit the corpus. Until you submit this review, the taxonomy is marked provisional and the Brain cannot be deployed.

Six kinds of problem can show up, each its own grouped inbox entry. **Anything not listed here is kept as drafted** — a category with no problem needs no decision from you:

- **Empty** — a category with no sections tagged to it at all. The default fix is to remove it, but you can keep a category you know will be used once more of the corpus is covered.
- **Barely used** — a category with only one or two tagged sections. Claude proposes merging it into the sibling it overlaps with, or keeping it if it is a real, distinct topic the corpus just mentions rarely.
- **Duplicates** — two or more category names that read as the same thing (compared by what the names themselves mean, not just their spelling; descriptions are not compared). Claude proposes merging the cluster into one.
- **Fits another category** — a sub-category (L2) whose tagged sections are actually about a different top-level category (L1) than the one it is drafted under. Claude proposes moving it.
- **Overloaded** — a top-level category with far more tagged sections than a typical one, usually because it is really several topics bundled together. Claude proposes splitting out new sub-categories and moving some existing ones under them.
- **Untagged content** — sections no category fits. Claude checks a sample of these by hand: some genuinely need a new category (see "propose new categories for the untagged sections" in the table above); many turn out to be filler with no topic at all (a chunk like "Okay." or "Yep."), which the classifier already marked `__no_topic__` rather than leaving unlabeled. Filler is not a taxonomy gap — you are not asked to add a category for it.

For example, on a real ~2,700-chunk corpus, classifying the draft against the corpus first cut what would have been 463 individual per-category keep-or-change items down to 48 grouped inbox entries: 61 label-similar pairs across 34 duplicate clusters, 116 empty categories, 129 categories with only 1–2 sections, 3 overloaded top-level categories, and 14 categories that fit better elsewhere. A 50-section sample of the untagged content found 46 were no-topic filler — real gaps, not 46 categories to invent.

## The four views

The app has four views; switch between them from the navigation. Keyboard shortcuts in the Inbox: **J**/**K** move, **A** accepts, **R** skips (or rejects), **U** undoes.

### Inbox

What is waiting for a decision. Each entry shows what Claude recommends, why, and what it would change (for example "moves 14 tagged sections").

- In the first-build review (and any later health review), entries are problems, and similar problems are **grouped**. "12 categories have no description" is one entry with a row per category. See **The first-build review** below for what the groups mean the first time.
- In a refine review, each entry is a proposed new category: **Approve**, **Rename…**, or reject it with a reason. A rejection sticks: the same proposal is not offered again.

**Grouped problems and Accept all.** In a group, you can accept or edit row by row, or click **Accept all remaining** to accept every row you haven't decided. Rows whose fix is only a safe default (no agent could propose a real fix, so the default is "leave it as is") are left out of Accept all for you to decide one at a time. So are rows that clash with another fix you accepted: describing a category you are merging away, for example. The app says how many it skipped and why.

**Skip** records nothing. It moves you to the next problem, and a skipped problem comes back the next time the taxonomy is checked.

**Redo with a note…** sends a fix back to Claude with your note, for example "shorter, and mention that it covers returns too". While the review is open, Claude revises it and the revised fix appears within seconds, marked **Revised by Claude**; you then accept it or not. If Claude isn't watching (another host, or the session ended), the note is queued and used the next time the taxonomy is checked. A naming-pattern entry (labels that differ only by a number or code, such as "Region 1", "Region 2") has no redo; change those in the Taxonomy view.

### Taxonomy

The whole tree, with how many sections each category tags, sample sections, and how much it overlaps with its siblings. Select a category to:

- **describe** it (or edit its description);
- **rename** it, **merge** it into another, **move** an L2 under a different L1, **split** it into several, or **remove** it. Each shows its impact before you confirm.

**+ New category** adds a top-level (L1) category; **+ Add sub-category** under an L1 adds an L2. A new category needs a description.

**Descriptions matter.** A category's description is shown to the agents that tag sections, so a described taxonomy tags more precisely. It is also stored in the graph, returned to kb (which uses it to say what a category means) and written into the Obsidian vault. One or two plain sentences are enough: what the category covers and how it differs from its siblings.

### Metrics

The metric inventory, in tabs by what each metric means for answering:

- **Needs a definition**: computable metrics with no governed definition. kb answers questions about these as "not modeled".
- **Possible duplicates**: pairs whose names are very alike. Merge them if they are one thing.
- **Quoted from documents**: figures the documents state. kb quotes them; it doesn't compute them.
- **Governed**: metrics kb computes from the governed metric layer.

You can add a metric or merge duplicates here. A health review can also propose a draft governed definition for a metric; if you accept it, Claude shows you the draft after you submit and asks whether to add it to the governed metrics file. The app never edits that file.

### Your changes

Everything you did in the Taxonomy and Metrics views, in one list. Withdraw anything you changed your mind about.

## Submitting

Click **Review & submit** (top right). The sheet lists every decision and its total impact. **Submit** sends it; undecided entries simply stay as they are. **Close the review without submitting** keeps your decisions saved but applies nothing.

After you submit, the tab says so and you can close it. Back in Claude Code, Claude:

1. applies exactly what you submitted, writing a new taxonomy version (`taxonomy/taxonomy_vN.json` and `taxonomy/current.json`); earlier versions are never changed;
2. rebuilds the graph, moving the tags of any category you renamed or merged;
3. re-tags the sections whose categories changed (removed or split categories send their sections back to be tagged again);
4. adds any tags you accepted in a health review, without removing any;
5. shows you any governed-metric drafts you accepted, and adds them only if you agree.

Every decision is recorded in `taxonomy/decisions.jsonl`, an append-only log: who decided what, when, and why a proposal was rejected. Commit it with the project, so the taxonomy's history travels with it.

## Without a browser

On a remote machine with no browser, Claude can export the review to a Markdown file instead. You fill in one `decision:` line per entry (`approve`, `reject: <reason>` or `amend: <change>`) and Claude imports it. The result is the same as submitting in the app.
