# fantasyagent

Sets your ESPN fantasy football lineup every week, with a veto window.

Saturday it works out the best legal lineup and shows it to you. Sunday morning
it re-checks the late injury news and submits — unless you told it not to.

```
Week 3 lineup — Shravan's Team

Proposed changes (+9.4 projected):
  RB -> BE: Rachaad White  (out)
  BE -> RB: Tyjae Spears
  FLEX -> BE: Jaylen Waddle  (on bye)
  BE -> FLEX: Khalil Shakir

Starters:
  QB    Jayden Daniels           WSH   21.3
  RB    Bijan Robinson           ATL   18.8
  RB    Tyjae Spears             TEN   11.2
  WR    Nico Collins             HOU   16.4
  WR    Garrett Wilson           NYJ   14.1
  FLEX  Khalil Shakir            BUF   12.0
  TE    Trey McBride             ARI   11.6
  D/ST  Ravens D/ST              BAL    7.4
  K     Jake Bates               DET    8.1
        TOTAL                         120.9
```

## What it actually decides on

Four signals, in descending order of how much they matter:

1. **Availability.** Anyone OUT, DOUBTFUL, on IR, suspended, or on a bye week is
   excluded outright. This is the whole ballgame — it is the one input that is
   knowable with certainty, and starting a ruled-out player is the most
   expensive routine mistake in fantasy. QUESTIONABLE is discounted 20% rather
   than excluded, so a star who might play still beats a healthy backup.
2. **Projections.** ESPN's weekly projections, already scored under *your*
   league's settings — so PPR, bonuses, and custom scoring are handled without
   you configuring anything.
3. **Matchup.** Fantasy points allowed per position by each defense, computed
   from the season's completed weeks under your scoring settings. Applied as a
   small multiplier (±12% at the extremes), because ESPN's projections already
   price in some matchup and a bigger adjustment would double-count it. Its job
   is breaking near-ties, not overriding a two-point gap.
4. **Kickoff times.** Players whose game has started are locked at ESPN and are
   never included in a move — the agent works around them instead of failing.

Slotting is solved as an assignment problem (Hungarian algorithm), not by
filling slots one at a time. Filling greedily strands points: put your best
remaining player in the FLEX and you can find yourself with no eligible starter
for WR2. The optimizer considers every legal arrangement at once.

## Setup

```bash
git clone https://github.com/shravanv0128-stack/fantasyagent
cd fantasyagent
pip install -r requirements.txt

cp config.yaml.example config.yaml   # league id, season, your team id
cp .env.example .env                 # ESPN cookies
```

**Getting your ESPN cookies.** ESPN has no public API and no API keys, so a
private league needs the session cookies from your browser. Log in at
fantasy.espn.com, open DevTools → Application → Cookies → `https://fantasy.espn.com`,
and copy two values:

- `SWID` — looks like `{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}`, keep the braces
- `espn_s2` — a long URL-encoded string

These are live session credentials for your whole ESPN account. Keep them in
`.env` or GitHub secrets, never in `config.yaml` (which is committed). They
expire every so often; when the agent starts reporting 401s, refresh them.

Your `league_id` and `team_id` are both in your league URL:
`.../football/team?leagueId=123456&teamId=3`.

## Use

```bash
python -m fantasyagent propose          # work out the lineup, save it, print it
python -m fantasyagent status           # show what is pending
python -m fantasyagent veto             # cancel this week's submission
python -m fantasyagent apply            # submit, unless vetoed
python -m fantasyagent apply --dry-run  # decide and print, write nothing
python -m fantasyagent run              # propose and submit in one shot
python -m fantasyagent --week 5 propose # override the scoring period
```

Start with `apply --dry-run` for a week or two. Read what it wants to do. Only
hand it write access once it stops surprising you.

### The veto window

`propose` never writes to ESPN. It saves the plan to `state/pending.json` and
tells you about it. `apply` then re-decides **from scratch** rather than
replaying Saturday's plan — Sunday's inactive reports are exactly the news worth
waiting for — and submits the fresh decision unless you vetoed.

A veto means "leave my lineup alone this week", not "reject these specific
moves". It is a hard stop.

`apply` also holds off if the new lineup beats the current one by less than
`min_gain` (default 0.5 projected points). Churning your roster for rounding
noise is worse than leaving it alone.

## Running it weekly

Two GitHub Actions workflows do the whole loop, with the veto living in a GitHub
issue so you can hit it from your phone:

- `propose-lineup.yml` — Saturday 11:00 ET. Computes the lineup and opens an
  issue with it.
- `apply-lineup.yml` — Sunday 12:00 ET. Skips if that issue has the `veto` label
  or a comment saying `veto`; otherwise submits and comments with the result.

Add these repository secrets:

| Secret | What it is |
| --- | --- |
| `ESPN_SWID` | Your `SWID` cookie |
| `ESPN_S2` | Your `espn_s2` cookie |
| `FANTASY_CONFIG` | The full contents of your `config.yaml` |
| `FANTASY_SLACK_WEBHOOK` | Optional — Slack incoming webhook for proposals |

`config.yaml` goes in a secret rather than the repo only so your league id stays
private; there is nothing sensitive in it otherwise.

Both cron times are UTC in the workflow files (`0 15 * * 6` and `0 16 * * 0`),
which assumes US Eastern. Adjust if your slate or timezone differs — and note
GitHub's scheduler can run up to ~15 minutes late under load, so leave margin
before kickoff.

**Nothing runs on your computer.** Both workflows execute on GitHub's runners on
GitHub's schedule. Your machine can be closed, off, or in another country; the
agent still sets your lineup. The only thing that has to be running is GitHub.

GitHub disables scheduled workflows after 60 days with no repository activity,
which would silently kill the agent mid-season. The apply job commits a
`.github/last-run` heartbeat every week to prevent that. If you clear out the
repo over the offseason, re-enable the schedules under the Actions tab before
week 1.

## Configuration

| Key | Default | Meaning |
| --- | --- | --- |
| `league_id` | — | From your league URL |
| `season` | — | e.g. `2026` |
| `team_id` / `team_name` | — | Which team is yours; one is enough |
| `min_gain` | `0.5` | Skip submitting below this projected improvement |
| `use_matchup` | `true` | Apply the defense-vs-position adjustment |
| `matchup_alpha` | `0.06` | How hard to lean on it, per standard deviation |
| `respect_locks` | `true` | Never touch a player whose game has kicked off |
| `state_dir` | `state` | Where the pending proposal and history live |

## Tests

```bash
pip install pytest && python -m pytest -q
```

The suite runs entirely against fixtures — no network, no credentials. It covers
the optimizer (including the greedy-vs-assignment case and locked players), each
signal, roster parsing, and the veto flow.

## Things worth knowing

**ESPN's API is undocumented.** Everything here targets endpoints ESPN uses for
its own web app. They have been stable for years but carry no compatibility
promise; if a season rolls over and things break, `fantasyagent/espn/constants.py`
and the `views` in `client.py` are where to look first.

**The agent only sets lineups.** It does not add, drop, or trade. Waivers are a
different problem with different risks, and nothing here can spend your FAAB.

**Projections are projections.** The availability logic is the part that
reliably makes you money; the optimizer will faithfully start whoever ESPN
projects highest, and ESPN is wrong plenty. If you have a strong read on a
player, veto and set it yourself — that is what the window is for.

**It cannot see your matchup.** It maximizes expected points, which is right in
most weeks but not all. If you are a 20-point underdog and need variance, it
will not know to swing for it.
