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

Starters                          Team  Proj   Why
  QB    Jayden Daniels            WSH   21.3   team implied for 27.5
  RB    Bijan Robinson            ATL   18.8   soft matchup by position
  RB    Tyjae Spears              TEN   11.2   leaning ceiling — you're an underdog this week
  WR    Nico Collins              HOU   16.4
  WR    Garrett Wilson            NYJ   14.1   18mph wind
  FLEX  Khalil Shakir             BUF   12.0
  TE    Trey McBride              ARI   11.6
  D/ST  Ravens D/ST               BAL    7.4   opponent implied for only 15.5
  K     Jake Bates                DET    8.1
        TOTAL                          120.9
```

## What it actually decides on

Every player evaluation runs through several independent signals, each capped
so it sharpens close calls rather than overriding the projection wholesale —
and each one that fires says so in the weekly email's "Why" column, so no
decision is a black box.

1. **Availability.** Anyone OUT, DOUBTFUL, on IR, suspended, or on a bye week is
   excluded outright. This is the whole ballgame — it is the one input that is
   knowable with certainty, and starting a ruled-out player is the most
   expensive routine mistake in fantasy. QUESTIONABLE is discounted 20% rather
   than excluded, so a star who might play still beats a healthy backup.
2. **Projections.** ESPN's weekly projections, already scored under *your*
   league's settings — so PPR, bonuses, and custom scoring are handled without
   you configuring anything. Everything below is an adjustment on top of this,
   not a replacement for it.
3. **Matchup.** Fantasy points allowed per position by each defense, computed
   from the season's completed weeks under your scoring settings.
4. **Game environment (Vegas).** Each team's implied point total, from the
   spread and total on ESPN's own public scoreboard. This is deliberately
   *not* "points allowed by position" — that stat is confounded by strength of
   schedule (a defense that faced Ja'Marr Chase and Justin Jefferson looks
   generous to WRs for reasons that have nothing to do with its coverage). The
   market's line prices in both offenses and both defenses for this specific
   game, and moves all week as news breaks. Defenses get the inverse: a low
   opponent implied total is good for your D/ST.
5. **Weather.** Sustained wind at outdoor stadiums downgrades the passing and
   kicking game, but only past a real threshold (15mph+) — a light breeze or
   ordinary rain isn't worth modeling, and it gets overrated relative to its
   actual scoring impact. A dome game is never touched by outdoor weather, and
   a retractable roof is treated like a dome rather than tracked live, since
   teams close it exactly when weather would otherwise matter.
6. **Your matchup.** This is the one that turns "maximize points" into
   "maximize your chance of winning." It fetches your opponent's roster,
   runs the same signals and optimizer over it, and compares your projected
   total to theirs. If you're a big underdog, players with a genuinely
   boom/bust history (computed from their own game-to-game scoring, nothing
   external) get a modest boost — you need variance, not the safest median
   outcome. If you're a big favorite, the same players get a modest discount
   in favor of steadier ones. In a close matchup this does nothing.
7. **Kickoff times.** Players whose game has started are locked at ESPN and are
   never included in a move — the agent works around them instead of failing.

All of it — Vegas lines and weather included — uses free, keyless public data
and degrades gracefully: any single source being unreachable just skips that
one signal for that run rather than failing the whole week, logged as a
warning so it's visible in the workflow's run log if something needs a look.

Slotting itself is solved as an assignment problem (Hungarian algorithm), not
by filling slots one at a time. Filling greedily strands points: put your best
remaining player in the FLEX and you can find yourself with no eligible starter
for WR2. The optimizer considers every legal arrangement at once.

**What this deliberately doesn't do:** read injury news, beat reports, or
practice-participation trends. Doing that well requires a paid LLM call per
player per week (interpreting a coach's quote or a beat writer's tweet isn't
something a free, keyless API can do) — a real cost and a new failure surface
this project hasn't taken on. If you have a read the numbers can't have, that
is exactly what replying `start`/`bench` to the weekly email is for.

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

### The email loop

If `email_to` is set, `propose` emails you the lineup — starters and bench in
a table, with a one-line reason for each ("18.4 pts projected", "on bye",
"lower projection than the starter at this position"). The email ends with
the exact words it understands:

```
approve                  lock this lineup in right now
veto                      leave your current ESPN lineup untouched
start <player name>      force that player into the lineup
bench <player name>      force that player out of the lineup
```

You reply from your phone, or don't reply at all — `apply` reads your inbox
Sunday morning before doing anything else. No reply means it goes ahead and
submits the lineup it already showed you. A reply's `start`/`bench` lines are
matched by name against your roster and folded into a re-optimized lineup
before submission; anything it can't match to exactly one player is skipped
and reported back rather than guessed at, and anything it doesn't recognize
at all (a full sentence, a question) is likewise left alone and noted.

Parsing is deliberately keyword-only — a misread free-text instruction that
silently benches the wrong player is worse than one that's ignored. If you
want a change it can't parse, reply with `start`/`bench` lines instead.

`apply` re-decides the whole lineup **from scratch** rather than replaying
Saturday's plan, so Sunday's inactive reports are exactly the news worth
waiting for — a swap request just gets folded into that fresh decision. A
`veto` reply is a hard stop: leave my lineup alone this week, not reject these
specific moves.

`apply` also holds off if the new lineup beats the current one by less than
`min_gain` (default 0.5 points) — unless you replied `approve` or sent a swap,
which are taken as explicit instructions and always go through.

If `email_to` is unset, none of this runs — `propose` still saves the plan and
prints/Slacks it, and `apply` still submits automatically on schedule.

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
| `FANTASY_CONFIG` | The full contents of your `config.yaml` (set `email_to` in it) |
| `GMAIL_ADDRESS` | The Gmail account the proposal is sent from |
| `GMAIL_APP_PASSWORD` | An [app password](https://myaccount.google.com/apppasswords) for that account (needs 2-Step Verification on) |
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
| `use_vegas` | `true` | Apply the Vegas implied-team-total adjustment |
| `use_weather` | `true` | Apply the wind downgrade for outdoor games |
| `use_volatility` | `true` | Tilt toward ceiling/floor based on your matchup margin |
| `respect_locks` | `true` | Never touch a player whose game has kicked off |
| `state_dir` | `state` | Where the pending proposal and history live |

## Tests

```bash
pip install pytest && python -m pytest -q
```

The suite runs entirely against fixtures — no network, no credentials. It covers
the optimizer (including the greedy-vs-assignment case and locked players),
every signal (availability, matchup, Vegas, weather, opponent-aware volatility),
roster parsing, and the veto/email flow.

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
