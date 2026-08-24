# Running it

```bash
docker compose up
```

The API is on `localhost:8000`, the UI on `localhost:3000`. Postgres and Redis come up
first and the API waits for them; the web container waits for the API's healthcheck.

## Credentials

Nothing here holds a credential. A project stores a *reference* and the operator puts the
value in the environment:

```bash
# .env beside docker-compose.yml
ACME_FIGMA_TOKEN=figd_…
ACME_JIRA_TOKEN=…
ANTHROPIC_API_KEY=…        # optional; without it the run does the deterministic sweep
```

Then the project stores `figmaTokenRef: "env:ACME_FIGMA_TOKEN"`. Two clients mean two
variables and two projects; neither token reaches the database, an artifact, or a log.
A bare value in the reference field is *refused* — that is the point of it.

## The database

Postgres, via psycopg 3. A plain `postgresql://` URL is accepted and rewritten to
`postgresql+psycopg://`, because that is the form Postgres itself prints and every
hosting provider hands out. SQLite is the fallback for running without a server:

```bash
DATABASE_URL=sqlite:///./bureau.db uvicorn --app-dir apps/api bureau_api.main:app
```

## Storage and retention

Artifacts live in the `artifacts` volume at `/srv/runs/{projectId}/{runId}`. Issues are
kept forever; screenshots are pruned beyond the last `BUREAU_KEEP_MEDIA_RUNS` runs
(default 10). A pruned run stays readable, re-checkable and diffable.

## Without Docker Desktop

[Colima](https://github.com/abiosoft/colima) is a lighter alternative and needs no GUI:

```bash
brew install colima docker docker-compose
colima start --cpu 4 --memory 8 --disk 60
```

Three things bit us on a clean macOS 14 / Apple Silicon machine, in case they bite you:

- **A stale `~/.docker/config.json`.** If Docker Desktop was ever installed, it leaves
  `"credsStore": "desktop"` behind and every pull fails with
  `docker-credential-desktop: executable file not found`. Remove that key and
  `"currentContext"`.
- **The compose plugin.** Homebrew installs it outside the CLI's search path. Add
  `"cliPluginsExtraDirs": ["/opt/homebrew/lib/docker/cli-plugins"]` to the same file.
- **DNS in the VM.** Cloud-init can leave `/etc/resolv.conf` as a dangling symlink to a
  systemd-resolved stub that never starts, so every pull fails with
  `lookup registry-1.docker.io on [::1]:53: connection refused`. Replace the symlink with
  a real file, and add a `provision:` block to `~/.colima/default/colima.yaml` so it
  survives a restart.

The first `colima start` can also hang on the disk resize; a `colima stop && colima start`
clears it.

## Developing against the containers

`apps/web/node_modules` and `apps/web/.next` are **container-local volumes**, not bind
mounts. A developer's `node_modules` holds darwin-arm64 binaries that do not run on Linux,
and an `npm ci` inside the container would overwrite the host's working copy on the way
past. Everything else is bind-mounted, so an edit on the host is live in the container.
