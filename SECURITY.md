# Security Policy

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, use GitHub's private vulnerability reporting for this repository:
**Security → Report a vulnerability** (the *Report a vulnerability* button on the
repository's Security tab).

When reporting, please include:

- A description of the issue and the class of problem (e.g. path traversal, secret
  exposure, injection).
- The affected skill/file and version or commit.
- Steps to reproduce, or a minimal proof of concept — described, not weaponized.
- The impact you believe it has.

We aim to acknowledge reports within a few business days and will keep you updated
on remediation.

## Scope

This toolkit runs locally and is designed to keep your data local: the knowledge
store is a portable SQLite file, and the optional Brain MCP HTTP transport is
**disabled by default**. Especially relevant reports include:

- Ways a skill or the MCP server could **exfiltrate local data** or read outside its
  intended paths.
- **Secret or credential handling** — anything that could cause keys to be logged,
  committed, or sent off-box (the deploy adapters read API keys only from
  environment variables and must never print them).
- **Untrusted-input handling** in the parsing/ingestion pipeline.

## Out of scope

- Vulnerabilities in third-party dependencies (report those upstream), though we
  welcome a heads-up so we can bump versions.
- Issues that require a already-compromised host.
