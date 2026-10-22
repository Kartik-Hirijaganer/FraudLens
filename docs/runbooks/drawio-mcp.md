# Runbook — draw.io MCP

## What and why

Use the official [draw.io MCP Tool Server](https://github.com/jgraph/drawio-mcp/tree/main/mcp-tool-server)
to open editable diagrams in the browser from Claude Code or Codex. The project pins
`@drawio/mcp@1.10.0` and runs it locally over stdio, with no account or API key required.
The hosted MCP App Server is a separate option; this setup uses the local Tool Server.

Repository diagrams remain Mermaid fenced blocks under `docs/`, per [AGENTS.md](../../AGENTS.md).
Use draw.io to inspect or edit Mermaid imports; keep the canonical Mermaid source current
and do not commit binary diagram exports.

## Setup

1. Make Node.js and npm available to the agent process. The pinned server requires
   Node.js 18 or later. Verify with `node --version` and `npx --version`.
2. Claude Code reads the project entry in [`.mcp.json`](../../.mcp.json). Restart the
   project session after changing it, then use `/mcp` to check that `drawio` is connected.
   Accept project-server trust if prompted.
3. Codex uses `.codex/config.toml`, which is machine-local and gitignored in this repo.
   Add the following table once, preserving existing settings:

   ```toml
   [mcp_servers.drawio]
   command = "npx"
   args = ["-y", "@drawio/mcp@1.10.0"]
   startup_timeout_sec = 60
   ```

   Codex only loads project configuration for trusted projects. Its desktop app, CLI,
   and IDE extension share MCP configuration; see the
   [official Codex MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).
4. From the project root, run `codex mcp get drawio` to confirm the effective entry.
   Restart the desktop MCP connection or start a fresh project session to load the tools.

The first connection downloads the pinned npm package into the npm cache. Subsequent
connections reuse it. No global package installation or application dependency is needed.

## Verify and use

Ask the agent to call `get_drawio_reference` with `format: "mermaid"`. A successful
reference response verifies a tool call over the connection. Then try:

> Use draw.io to open a Mermaid flowchart: Browser → Vercel → Azure Container Apps →
> Supabase. Show ChromaDB as a separate dependency of the backend. Return the Mermaid
> source so I can keep it in the architecture documentation.

`open_drawio_mermaid` opens the editor in the default browser. `open_drawio_xml` supports
native draw.io shapes, and `search_shapes` finds cloud-library shapes. Only pass synthetic
or public architecture content, preserving the project's PHI and secrets rules.

## Troubleshooting and removal

- **`npx` not found:** ensure the desktop process inherits the Node installation's PATH,
  then restart it. With a version manager, a terminal may have a different PATH.
- **Startup timeout:** run `npx -y @drawio/mcp@1.10.0` once in a terminal to populate
  the npm cache, then stop it with Ctrl-C and reconnect. The server waits for MCP input.
- **Tools missing:** confirm the project is trusted, check `/mcp`, and restart the connection.
  `codex mcp get drawio` verifies configuration only; the reference call verifies execution.
- **Remove:** delete only the `drawio` entry from `.mcp.json` and the
  `[mcp_servers.drawio]` table from the local `.codex/config.toml`, then restart the connection.
- **Upgrade:** review the official release, update both version pins together, and repeat
  the reference-call check before using the new version.
