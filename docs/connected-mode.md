# DayPilot connected mode

DayPilot keeps one curated MCP contract for every capability. LangGraph discovers the
same semantic tools in every mode; each existing MCP server selects its provider adapter
at runtime.

```text
LangGraph → MultiServerMCPClient → DayPilot MCP server
                                  ├─ demo SQLite (demo mode)
                                  ├─ Composio hosted MCP → Google Super / Twitter (managed)
                                  ├─ direct Google/X API adapters (advanced)
                                  └─ allowlisted local folders (local Files MCP)
```

## Local setup

The normal connected path uses one server-only Composio project key. No Google Cloud
OAuth client or X developer credentials are needed for managed connections.

1. Create or sign in to a Composio project and copy its API key.
2. Copy `.env.example` to `.env` and set:

   ```dotenv
   COMPOSIO_API_KEY=...
   DAYPILOT_DEMO_MODE=false
   DAYPILOT_PROVIDER_MODE=managed
   ```

3. Start DayPilot and open Preferences → Connected services.
4. Click Connect Google, complete the hosted Composio authorization, then repeat with
   Connect X. Add local Files folders with Manage folders.

DayPilot never asks for tokens in chat and never sends access or refresh tokens to the
frontend. Composio Connect Links and callback results contain only short-lived
redirect/status data. The stable local Composio user ID is generated once and stored in
the DayPilot database so connections survive restarts.

## Google Workspace

Managed Google uses the current `googlesuper` Composio toolkit so Gmail, Calendar, and
Tasks share one hosted connection. DayPilot creates one Composio session with a fixed,
curated allowlist of the semantic operations it needs, then connects to that session's
hosted MCP endpoint. Composio owns OAuth, token refresh, and provider credentials
([managed authentication](https://docs.composio.dev/toolkits/managed-auth),
[sessions via MCP](https://docs.composio.dev/docs/sessions-via-mcp)).

DayPilot also retains the existing direct Gmail/Calendar/Tasks adapters. To use those
instead, set `DAYPILOT_PROVIDER_MODE=direct` (or individual provider selectors) and
configure the `GOOGLE_*` values marked as advanced in `.env.example`.
Direct mode uses `http://localhost:8000/api/connections/google/callback` as its default
Google redirect and the analogous `/api/connections/x/callback` route for X.

Gmail and Calendar have official Google Workspace remote MCP servers in public Developer
Preview ([Google configuration](https://developers.google.com/workspace/guides/configure-mcp-servers)).
The local managed path uses Composio's hosted MCP session because it provides the
browser authorization and account lifecycle required by this app.

## X

Managed X uses Composio's current `twitter` toolkit and hosted Connect Link. If a
Composio project or account reports that the managed app is unavailable, DayPilot shows
an explicit unavailable/error state and never silently switches to direct X OAuth.
`create_post_draft` remains a local DayPilot draft; `publish_post` is the only external
public write and remains approval-gated.

Direct X OAuth 2.0 with PKCE remains available as an advanced mode through the
`X_CLIENT_ID`, `X_CLIENT_SECRET`, and `X_REDIRECT_URI` settings.

## Local Files

Files remains read-only. A user explicitly adds folders in Preferences; the backend
canonicalizes every path, rejects filesystem-root access, blocks traversal and symlink
escapes, blocks credential-like files, limits recursion/results/size, and returns
relative file identifiers rather than arbitrary paths to tools. A hosted backend cannot
read a user's Mac directly; local Files is therefore intended for local development or
a future companion bridge.

## Safety

- Reads remain autonomous; writes remain code-classified and approval-gated.
- Managed provider adapters are reached through a Composio hosted MCP session; the graph
  never calls the Composio SDK or provider APIs directly.
- The small server-side Composio SDK surface is limited to creating/reusing sessions,
  generating Connect Links, checking account state, and disconnecting accounts; it is
  not used as a second tool-execution path.
- Provider failures never fall back to demo data.
- Demo reset is only available while `DAYPILOT_DEMO_MODE=true` and never calls real
  provider APIs.
- Managed disconnect removes/revokes the Composio connected account where supported; it
  does not delete provider objects or DayPilot history. Direct disconnect removes the
  local encrypted credential record.
- Composio owns managed provider credentials. The DayPilot Fernet store is retained only
  for direct mode and is backend-only with mode-600 local files; production should
  replace it with a host secret manager or keychain.
