# Butler skill

The short version. Each section links to the full guide: [butler-site-skill-guide.md](butler-site-skill-guide.md).

## What it is

A **Butler skill** for a website lets Butler act on it, such as a perps or prediction site. It is
a normal skill repo with one extra file:

```
butler-skill-<site>/
  SKILL.md          when Butler uses the site, and what it says     → Butler reads it
  venue.yaml        the allow-list + the steps for each action      → the server runs it
  venue.test.yaml   the cleaned recording, used as the test
  record/*.py       the Playwright scripts you recorded
```

No per-site code goes into `bevo-server`. The server has one shared engine that runs every
`venue.yaml`. → [Part 1](butler-site-skill-guide.md#part-1-the-idea-in-one-example)

## The one rule

> The steps can be wrong. The money cannot be wrong.

A wrong step stops the run, with nothing sent. Every request or signature that could move
money is held by the server, and goes out only if `venue.yaml`'s allow-list names it **and**
it matches the card the owner approved. Once, never twice. → [Part 2](butler-site-skill-guide.md#part-2-the-four-safety-stops)

## Before you start (5 minutes)

Check the site: we may automate it (terms of service), we know how money moves (wallet popup
or login alone), trades are not sent over a websocket, the chain is in `chains.ts`, we know
the blocked countries, and no KYC or invite is needed.
→ [Part 4](butler-site-skill-guide.md#part-4-does-my-site-need-a-venue-description-check-this-first-5-minutes)

## The steps

```
1. Record     python3 scripts/site.py record <site> --repo ../butler-skill-<site> ...
              (from ~/bevo/butler-skills; you click each action once, $1-$5, test account)
              then: site.py replay ... and site.py try-calls <site>
2. Draft      paste the Step 2 prompt into Claude Code → venue.yaml + SKILL.md
3. Test       not available yet (needs the engine); replay the scripts for now
4. Review     read every line of `allow`; check against security-rules.md
5. Try it     on staging, from your branch (the development loop below)
6. Release    merge, bump the version, tag v1.3.0, then one PR to butler-skills:
                skills.json  "ref": "v1.3.0"
7. Live       ~5 minutes after that PR merges. No server deploy.
```

→ [Part 5](butler-site-skill-guide.md#part-5-step-by-step)

**Call or click?** Use the site's API (`call`) whenever it works: it is faster and survives a
page redesign. Click only when the page builds the request itself (a signature, a one-time
token, a captcha). → [Part 5, Step 2](butler-site-skill-guide.md#part-5-step-by-step)

## The development loop (staging)

```
1. edit     venue.yaml (and SKILL.md)
2. push     git push origin feat-x
3. install  only if SKILL.md changed: tell your test Butler
              "install github.com/Virtual-Protocol/butler-skill-<site>/tree/feat-x"
4. try      "long ETH $5 on Acme" → approve the card
5. check    the result on chain; back to 1
```

Staging fetches `venue.yaml` from your branch. Production never does: it runs only the tag
in `skills.json`. → [Part 6](butler-site-skill-guide.md#part-6-development-build-and-test-on-staging)

## Releasing a new version

- Every change bumps the version (`SKILL.md` + `CHANGELOG.md`). The hub refuses a changed
  file under an old version, and that fails the build for every skill.
- Merge in the skill repo freely. Nothing reaches owners until the `butler-skills` PR merges.
- That PR is the review: `butler-maintainers` read the `venue.yaml` diff since the last tag.

→ [Part 5, Step 6](butler-site-skill-guide.md#part-5-step-by-step)

## Security: the must-dos

- Everything the card promises (a price, a limit) is enforced in `allow`, never only in a step.
- Money only ever goes out to the owner's own Butler wallet. Never to an address from args.
- List every API origin the site uses, not only the app's.
- Card text comes only from typed args and the server's own reads. Never page text.
- Never commit cookies, tokens, raw HAR or traces. Screenshots stay on your laptop.

→ [security-rules.md](security-rules.md) (all 12 rules)

## Where the details are

All in [butler-site-skill-guide.md](butler-site-skill-guide.md):

```
Part 1-3    the idea, the two flow pictures, the safety stops, who writes what
Part 4-6    the fit check, every step in full, the development loop
Part 7      venue.yaml, step types, the skill folder, SKILL.md
Part 8-9    levels 0 and 1, which version runs (the hub index)
Part 10     rules for writing steps
Part 11     points to security-rules.md (S1-S12, its own file)
Part 12-13  what Bevo must build, open questions
```
