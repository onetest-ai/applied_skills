# Primo Brain — Team Installation Guide

This guide installs the **Primo Brain** plugin into your Claude Desktop.
It connects Claude to the Primo team knowledge base so you can search metrics,
documents, and approved company sources directly from Cowork sessions.

Prerequisites: Claude Desktop with Cowork enabled.

---

## Step 1 — Get your API key

Request your personal Primo Brain API key from **[YOUR TEAM LEAD / CHANNEL]**
before starting. You will need it in Step 3.

> The key is stored securely in your system keychain after you enter it once.
> Never paste it into a file, chat message, or email.

---

## Step 2 — Download and install the plugin

1. Download **`primo-brain-1.0.0.zip`** from the SharePoint page:
   **[YOUR SHAREPOINT LINK]**

2. Open **Claude Desktop** → click your avatar or the settings icon →
   **Customize** → **Plugins** → **Add local plugin**

3. Select the downloaded `primo-brain-1.0.0.zip` file and click **Open**

4. Click **Enable** on the Primo Brain plugin card

---

## Step 3 — Enter your API key

As soon as you click Enable, Claude Desktop shows a prompt:

> **"API key for Primo Brain MCP server"**

Paste your API key and confirm. It is stored in your system keychain and
never written to any file. You will not be asked again unless you reinstall.

---

## Step 4 — Verify the connection

1. Open a **new Cowork task** (existing tasks will not have the plugin loaded)
2. Type `/primo-brain` — it should appear as a recognised skill
3. Activate the skill and ask: *"Call health and list available metrics."*

If it responds with metrics, you are ready to use the brain.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `/primo-brain` not found | Remove the plugin and re-add the ZIP (Plugins → three-dot menu → Remove) |
| API key prompt never appeared | Remove and re-enable the plugin |
| "Connection error" in responses | Check with **[YOUR TEAM LEAD / CHANNEL]** — the key may need to be reissued |
| Still open task, no skill | Close the task and open a new one |

---

## Offboarding

If you leave the project, notify **[YOUR TEAM LEAD / CHANNEL]** so your API key
can be revoked. Remove the plugin via Customize → Plugins → three-dot menu → Remove.
