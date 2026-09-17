# Primo Brain — Team Installation Guide

This guide installs the **Primo Brain** plugin into Claude Cowork on **Windows or macOS**.
It connects Claude to the Primo team knowledge base so you can search metrics,
documents, and approved company sources directly from Cowork sessions.

**Prerequisites:** Node.js 18+, CodeMie CLI, Claude Cowork app.

---

## Step 1 — Get your API key

Request your personal Primo Brain API key from **[YOUR TEAM LEAD / CHANNEL]**
before starting. You will need it in Step 4.

> Never paste the key into a chat message, email, or config file.
> The installer stores it in a file readable only by your Windows user account.

---

## Step 2 — Set up the CodeMie Gateway (one-time)

The Gateway is what connects Claude Cowork to the MCP bridge on your machine.

```powershell
codemie proxy connect --claude-desktop --url https://codemie.lab.epam.com
```

Run this once. If the command is not found, ask **[YOUR TEAM LEAD / CHANNEL]**
for the CodeMie CLI installer.

---

## Step 3 — Extract the plugin package

1. Download **`primo-brain-cowork-1.0.0.zip`** from **[YOUR SHAREPOINT LINK]**
2. Right-click → **Extract All** → choose a permanent folder, e.g. `C:\tools\primo-brain`

   > Use a permanent location — the installer copies files from here.

3. Inside the extracted folder, copy the example config:

   ```powershell
   cd C:\tools\primo-brain
   Copy-Item brain.config.example.json brain.config.json
   ```

   The default `brain.config.json` is already correct — no edits needed unless
   you were given a different endpoint.

---

## Step 4 — Add your API key

Create a `.env` file next to `brain.config.json`:

```powershell
"PRIMO_BRAIN_API_KEY=<paste-your-key-here>" | Out-File .env -Encoding ascii
```

Replace `<paste-your-key-here>` with the key you received in Step 1.

---

## Step 5 — Run the installer

```powershell
node scripts/install-windows.mjs
```

The installer will:
- Store your credentials securely in `%LOCALAPPDATA%\Primo Brain\` (your user only)
- Register a background bridge service that starts automatically at logon
- Wire the bridge into the CodeMie Gateway
- Restart Claude Cowork
- Build a plugin ZIP in the `dist\` folder

You should see a line like:
```
✓ Primo Brain installed.
  Upload dist\primo-brain-cowork-1.0.0.zip in Customize → Plugins → Add.
```

**macOS users:** run `node scripts/install.mjs` instead.

---

## Step 6 — Upload the plugin in Cowork

1. Open **Claude Cowork**
2. Click your avatar → **Customize** → **Plugins** → **Add local plugin**
3. Select `dist\primo-brain-cowork-1.0.0.zip`
4. Click **Enable**

No API key prompt will appear — the key is already in the bridge from Step 4.

---

## Step 7 — Verify the connection

1. Open a **new Cowork task** (existing tasks will not pick up the plugin)
2. Type `/primo-brain` — it should appear as a recognised skill
3. Activate the skill and ask: *"Call health and list available metrics."*

If it responds with metrics, you are ready to use the brain.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `codemie: command not found` | Ask **[YOUR TEAM LEAD / CHANNEL]** for the CodeMie CLI installer |
| `brain.config.json not found` | You skipped the Copy-Item step in Step 3 |
| `PRIMO_BRAIN_API_KEY not found` | Check `.env` exists and the key is on one line with no extra spaces |
| `CodeMie Gateway not configured` | Re-run Step 2: `codemie proxy connect --claude-desktop` |
| Bridge did not become healthy | Check logs at `%LOCALAPPDATA%\Primo Brain\logs\stderr.log` |
| `/primo-brain` not found in Cowork | Remove the plugin and re-add the ZIP (Plugins → three-dot menu → Remove) |
| Connect button greyed out | You uploaded the wrong ZIP — use `dist\primo-brain-cowork-1.0.0.zip`, not any other file |
| Still broken after reinstall | Contact **[YOUR TEAM LEAD / CHANNEL]** with the contents of `%LOCALAPPDATA%\Primo Brain\logs\` |

---

## Offboarding

If you leave the project, notify **[YOUR TEAM LEAD / CHANNEL]** so your API key
can be revoked. To uninstall:

```powershell
# Remove the scheduled task
schtasks /Delete /TN primo-brain-mcp-bridge /F

# Remove plugin from Cowork
# Customize → Plugins → three-dot menu → Remove

# Remove local files (optional)
Remove-Item -Recurse "$env:LOCALAPPDATA\Primo Brain"
```
