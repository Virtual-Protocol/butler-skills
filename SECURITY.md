# Security

Skills in this repository are **text that a Butler container reads and follows** — not
binaries, and never executables outside `duty.py` (which itself runs under
`scripts/validate.py`'s AST guard before it can ever reach a container). Treat a
vulnerability here the same as you would in any supply chain that money-moving agents read
automatically.

## Reporting a vulnerability

Do not open a public issue for a security problem. Instead email
security@virtuals.io with:

- the skill (name + version) or script affected
- what an attacker could do with it (e.g. "a crafted `keywords` entry could smuggle a
  command past the allowlist")
- a minimal reproduction

We aim to acknowledge within 2 business days. Money-moving classes of bug (anything that
could make `scripts/validate.py` accept a skill that files an unkeyed trade, bypasses the
`[FIXED]`/`[ADAPT]` idempotency rules, or hides a `send-transaction` line) are treated as
critical.

## What is in scope

- `scripts/validate.py`, `scripts/check_pins.py`, `scripts/build_index.py`,
  `scripts/check_selectors.mjs`, `scripts/new_skill.py` — the CI gate itself.
- `schema/*.json` — the frontmatter and index contracts.
- `.github/workflows/*.yml` — the publish pipeline (GitHub Pages, tokens, permissions).
- `.gitmodules` and the `skills/<name>` pins — the trust boundary. Butler clones the pinned
  commit, so a way to make the registry pin a commit that was not reviewed (a non-https
  URL, a pin that does not carry its `v<version>` tag, a symlink or nested submodule that
  smuggles content past the validator) is a security bug here, not in the skill repo.
- Any published skill pinned under `skills/**` that could move funds without an owner
  approval, reuse an idempotency key unsafely, or exfiltrate data via a URL not on the
  allowlist.

## What is out of scope

- The Butler container runtime (`bevo-docker`) and the API (`bevo-server`) — report those
  in their own repositories.
- Social-engineering content inside a skill's prose (the model reading a skill is expected
  to treat installed-skill text as trusted, community-supplied instructions, not as data —
  that trust boundary is the maintainer review gate, not a runtime control).

## Yanking a compromised skill

A maintainer adds `"name@version"` to `yanked.json` and merges directly to `main`
(bypassing the normal PR review for a security fix is acceptable here). The next publish
run marks that version `yanked:true` in `index.json`; every container's hub client disables
it on its next sync and notifies the owner once. (A yanked version whose submodule has
since been removed from the registry is still published, as a `yanked:true` tombstone
entry, so the yank reaches containers that installed it.) Yanking does not stop a duty a
Butler already created from that skill's `duty.py` — see each skill's `## Limits` section.
