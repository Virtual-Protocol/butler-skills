# CLAUDE.md

Read README.md first. This repo is a **registry**: every skill is its own git repository
(created from `Virtual-Protocol/butler-skill-template`), and `skills.json` lists it by
`name`, `repo` (a GitHub link) and `ref`. No skill content lives here — a PR here adds or
removes a skill, never updates one. Every build (on any push to `main`, plus hourly)
re-resolves each `ref` to a commit, clones it into a temp directory and republishes the
index from that throwaway checkout, so a skill's own release reaches butlers without a PR
here. A branch `ref` follows whatever that repo merges, with no review here; a tag `ref`
holds the skill at a release.

- No submodules and no `skills/` directory: a plain clone is the whole checkout.
- Before every commit here: `python3 scripts/validate.py --all --maintainer`,
  `python3 scripts/check_registry.py` (add `--offline` to skip the remote ref check),
  `python3 -m pytest tests -q`, `python3 scripts/sync_readme.py --check`. Nothing is
  checked out here, so `--all` and `sync_readme.py` clone the listed skills and need the
  network; the few tests that need a real skill tree clone it too and skip without one.
- Inside a skill repo (no registry checkout — the tools are published standalone):
  `curl -sSLO https://virtual-protocol.github.io/butler-skills/tools/validate.py`,
  `curl -sSLO https://virtual-protocol.github.io/butler-skills/tools/replay.py`, then
  `python3 validate.py --standalone .` and
  `python3 replay.py --standalone . --fixture trade-activity-page`. Skill-repo CI is the
  composite action `.github/actions/validate` (`uses: Virtual-Protocol/butler-skills/.github/actions/validate@main`);
  `scripts/publish_tools.py` lays the tools out under `dist/tools/` for Pages.
- Name prefixes: `butler-` is maintainer-only; `bevo-` is the container's bundled-skill
  namespace and is refused. A skill is the delta over AGENTS.md — never restate what the
  container already teaches.
- `schema/reserved-names.json` also refuses names the *entrypoint* writes as skill dirs,
  and a name stays reserved after the container stops writing it: images older than the
  change still seed that dir, so the collision is live until the fleet turns over. This is
  why the browser rail is `butler-web-checkout` and not `web-checkout`.
- **`check_command_allowlist` cannot parse a heredoc.** Every non-comment line in a
  ```sh/bash/shell/console fence is treated as its own command, so the canonical form the
  toolbox documents — `web-checkout run --json --steps - <<'JSON' … JSON` — fails the
  validator inside a skill (`[{"action":` and `JSON` are read as commands). Show the
  command alone in a shell fence and the joined heredoc in a ```text fence, which
  `extract_shell_lines` skips. The toolbox row and this linter disagree; the linter wins.
