# Using kb in Claude Cowork

`kb` is the same plugin in Claude Code (CLI) and in Claude Cowork (Desktop).
Cowork keeps its own plugin install state — installing kb in the CLI does not
make it appear in Cowork — so install it in Cowork explicitly, then point it at
your project's Brain via a remote connector. Nothing runs on your machine; the
connector runs in Anthropic's cloud, so setup is identical on macOS, Windows,
and Linux.

## 1. Install the kb plugin

1. Open **Customize** from the left sidebar.

   ![Open Customize from the Claude Desktop sidebar](images/cowork-01-open-customize.png)

2. Go to the **Plugins** tab, then **Add → Add marketplace**.

   ![Plugins tab with the Add menu open on Add marketplace](images/cowork-02-plugins-add-marketplace.png)

3. Choose **Add from a repository**.

   ![Add marketplace dialog with Add from a repository highlighted](images/cowork-03-add-from-repository.png)

4. Select the `applied-ai` marketplace repository — **onetest-ai/applied_skills**.

   ![Selecting the onetest-ai/applied_skills repository](images/cowork-04-select-applied-skills-repo.png)

5. Install **kb** from that marketplace and enable it.

> **Air-gapped alternative:** instead of a marketplace, upload a plugin ZIP of
> `bundles/kb` under **Customize → Plugins → Add → Upload plugin** (≤50 MB).
>
> ![Add menu with Upload plugin highlighted](images/cowork-alt-upload-plugin-menu.png)
>
> ![Upload a plugin drag-and-drop area](images/cowork-alt-upload-plugin-dropzone.png)

## 2. Add your project's Brain as a connector

Each project has its own Brain endpoint. Add it once per project:

1. Go to the **Connectors** tab and click **Add**.

   ![Connectors tab with the Add button highlighted](images/cowork-05-connectors-add.png)

2. In **Add custom connector**, give the connector a name that identifies the
   project (e.g. `acme-brain`) and paste your project's **HTTPS MCP URL**
   (Streamable HTTP transport).

   ![Add custom connector dialog: the connector name and MCP server URL fields](images/cowork-06-add-connector-name-brain.png)

   > The screenshot shows `brain` in the name field — that is just the example name it was
   > captured with, not a requirement. Any name works.

3. Authorize with **Entra OAuth** (Advanced settings → OAuth client id/secret).
   If your endpoint uses a static key, set an `X-API-Key` header instead.
4. The name is yours to choose — **any name works**. kb finds a Brain by the tools it
   exposes, not by what the connector is called; a project-specific name just makes the
   choice readable when several Brains are connected.
5. Enable the connector.
6. **Pin it in this project's instructions.** Add one line naming the Brain to the project
   instructions Cowork surfaces for this project (kb never writes this file — add the line
   yourself):

   ```
   This project's Brain is `acme-brain`.
   ```

   A pinned Brain is the first thing kb resolves — every skill checks it before discovery,
   so a project with one connector never has to disambiguate, and a project with several
   always answers from the one you named. If the pin names a Brain that isn't reachable, kb
   stops and tells you which Brains ARE reachable instead of silently answering from a
   different one.

**In two projects?** Add both Brains as connectors and leave both enabled. kb discovers
every reachable Brain and, when more than one answers, asks which to use — listing each by
name and by the analytical goal it reports. You can answer in advance by naming it in the
request ("ask the acme brain about Q3 handle time"), or pin one per project as above.

## 3. Verify

Ask a simple question with `/kb:ask` — e.g. "what data does this Brain cover?" — and
confirm the answer cites the Brain you expect (it names which Brain it used before
answering). Then use `/kb:explore`, `/kb:challenge`, `/kb:brief`, `/kb:report`.

## What differs from the CLI

- **Ambient mode (`/kb:mode`)** relies on a hook that does not fire in Cowork.
  In Cowork, ground answers by invoking the kb skills explicitly.
- **The SessionStart health line** reads a local store and is CLI-only.
- **Choosing between Brains** is remembered for the length of a conversation only. Cowork
  has no project-local state for kb to write, so with several Brains connected it asks
  once per conversation rather than once per project.
- **Approval prompts.** The CLI's escape hatch — adding a server to `permissions.allow` in
  `.claude/settings.json` — doesn't apply here: Cowork doesn't surface a
  `.claude/settings.json` file for this project, so there's nothing to edit. Approve Brain
  tool calls as Cowork prompts for them.
