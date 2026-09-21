# Security

Templates in this registry are **Python that a Butler container runs unattended** — a
`duty.py` may shell `acp trade` and move the owner's money, gated only by the
`scripts/validate.py` AST checks it must pass before it can ever be created as a duty.
Treat a vulnerability here the same as you would in any supply chain that money-moving
agents run automatically.

## Reporting a vulnerability

Do not open a public issue for a security problem. Instead email
security@virtuals.io with:

- the template (id + version) or script affected
- what an attacker could do with it (e.g. "a crafted `duty.py` shape could smuggle a
  money command past the idempotency-key check")
- a minimal reproduction

We aim to acknowledge within 2 business days. Money-moving classes of bug (anything that
could make `scripts/validate.py` accept a template that shells an unkeyed `acp trade`,
calls a retired SDK verb the runtime no longer implements, or hides a
`send-transaction`-shaped command) are treated as critical.

## What is in scope

- `scripts/validate.py`, `scripts/check_registry.py`, `scripts/build_index.py`,
  `scripts/new_skill.py`, `scripts/remove_skill.py` — the CI gate itself.
- `schema/*.json` — the `recipe.json` and index contracts.
- `.github/workflows/*.yml`, `.github/actions/validate/*` — the publish pipeline
  (GitHub Pages, tokens, permissions) and the composite action every template repo's CI
  runs.
- `templates.json` — the trust boundary. It holds no template content: a name, a GitHub
  link and a `ref`. Every build re-resolves each `ref` to a commit, clones it, and writes
  that resolved commit plus a sha256 for every file into the index, so a container can
  verify exactly what it fetched — but the registry does not review what it resolves.
  **With a branch `ref`, whatever a template repo merges reaches butlers on the next
  build (hourly, or on any push here) with no review in this repo.** A template that
  should move only on release gets a tag `ref`; that is the only release gate the
  registry has. A way to make a build resolve something nobody listed (a non-https or
  credentialed `repo` URL, another host, a `ref` that is not a plain branch or tag name,
  a symlink or nested repository that smuggles content past the validator) is a security
  bug here, not in the template repo.
- Any template listed in `templates.json` whose `duty.py` could move funds without an
  owner approval, reuse an idempotency key unsafely, or exfiltrate data via a URL not on
  the allowlist.

## What is out of scope

- The Butler container runtime (`virtuals-agent`) and the API (`bevo-server`) — report
  those in their own repositories.
- The runtime's own enforcement of the money and idempotency rules once a duty is
  running (the AST checks here are a filing-time gate on the code a template ships; the
  container's own `bevo_ast.py`/`lint.mjs` re-check the same facts at `duty_create`, and
  is the authority when the two disagree).

## Yanking a compromised template

A maintainer adds `"id@version"` to `yanked.json` and merges directly to `main`
(bypassing the normal PR review for a security fix is acceptable here). The next publish
run drops that version from the live index (or, if the template is still listed at a
different version, keeps only the safe one) and — for a version whose template has been
fully removed from `templates.json` — publishes a tombstone entry with no `files` and no
`source`, so a fresh `duty_create` against that ref fails loudly instead of installing it.
Yanking does not touch a duty a Butler already created from that template's `duty.py` —
the container never re-fetches a template after create; stopping an already-running duty
is the owner's own pause/delete action in the app, not something this registry can do.
