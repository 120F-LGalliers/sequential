# Group sequential testing engine — v0.4 (core + Streamlit app + decision guidance)

First-pass implementation of 120F's group sequential testing tool, following
the AGILE-style method referenced in the project brief (analytics-toolkit.com
/ Georgi Georgiev): a Group Sequential Test with Efficacy and Futility
boundaries (GSTEF), built on Kim-DeMets/Lan-DeMets error-spending functions,
O'Brien-Fleming as the default spending function, non-binding futility.

Now supports one-sided or two-sided testing and more than one variant vs. a
shared control (Bonferroni-adjusted), both as user choices in the Design
page — on top of the Streamlit app added last pass. This pass adds a
suggested check-in cadence calculator and, across the app, contextual
guidance at the actual judgment calls an analyst has to make (baseline/MDE
estimation, alpha/power/looks trade-offs, futility, novelty effects, and
allocation assumptions) — see "Decision support added this pass" below.

## What's implemented

**Statistical core (`engine/`)**
- `spending_functions.py` — O'Brien-Fleming-type, Pocock-type, and
  Kim-DeMets power-family error-spending functions (alpha or beta).
- `boundaries.py` — the core numerical method: recursive numerical
  integration over the canonical joint (Brownian-motion) distribution of the
  sequence of test statistics, to solve for efficacy and futility boundaries.
  Same class of algorithm used by reference software (R's gsDesign/rpact).
- `design.py` — design-stage calculator: baseline/MDE/alpha/power/looks →
  boundary table, max sample size, inflation factor vs. fixed-horizon,
  expected N under H0/H1. Binary and continuous metrics. `sides`
  ("one"/"two") and `n_variants` (Bonferroni-adjusted) as first-class inputs.
  **New**: `suggest_monitoring_cadence()` — given expected weekly traffic,
  projects how long the test will take to reach its max sample size and
  suggests a check-in interval that spreads the planned `n_looks` evenly
  across that duration, plus a "don't check before this many days" floor.
  Purely a planning aid (see the Design page section below) — it doesn't
  feed back into the boundaries themselves.
- `interim.py` — monitoring-stage analysis. Given cumulative data at a look,
  computes the Z-statistic and RECOMPUTES boundaries for the actual observed
  information fraction (not just the pre-planned equally-spaced looks) —
  this is what lets real-world monitoring drift from the plan (missed weeks,
  extra looks) without breaking error control. **New**: analyzes every
  variant against the shared control in one call, returns a
  continue/stop-efficacy/stop-futility/stop-significant-loss decision per
  variant.
- `validate.py` — validation harness (`python3 -m engine.validate`), now 5
  independent checks (see below).

**Streamlit app (`app/`)**
- `Home.py` — landing page, explains the two-step flow, flags validation
  status, and now has a clear "nothing is saved" banner.
- `pages/1_Design_a_Test.py` — the design calculator as a form. Sides
  (one/two) and Number of variants inputs; the boundary table/chart show
  the lower ("significant loss") boundary when two-sided. **New**: help
  text on every input that involves real judgment (baseline/std-dev
  estimation window, MDE as "smallest effect worth acting on" vs. sample-size
  cost, alpha/power framed for CRO decision stakes, why more `n_looks`
  is nearly free under O'Brien-Fleming, when to skip futility); an optional
  "expected weekly traffic" input that, once a design is computed, surfaces
  a suggested check-in cadence (projected weeks to max sample, suggested
  interval, and a minimum-information floor before the first check).
- `pages/2_Monitor_a_Test.py` — manual interim data entry, one row per look,
  with a column pair per variant when more than one is in the design →
  a decision + trajectory chart PER VARIANT. Manual entry is deliberate (no
  Adobe server-to-server credentials yet) — this page is where an automated
  "pull latest from Adobe" button would slot in later, without engine/
  changing. A prominent "nothing on this page is saved" banner —
  there's no storage layer (see open questions), so re-entering data on
  every visit is the expected workflow for now, not a bug. **New**: a
  "Considerations before you act on a result" expander (novelty/day-of-week
  effects, the equal-allocation assumption behind the info-fraction calc,
  cadence reminder) plus an inline nudge under any efficacy/significant-loss
  stop that happened before 50% information, flagging it as an early result
  worth a sanity check before acting on.
- `.streamlit/config.toml` + `app/_theme.py` — light 120F brand pass (brand
  orange primary, warm off-white surfaces, Manrope/Archivo Black/JetBrains
  Mono type). Kept to the "quick/internal tool" end of the brand skill's
  guidance rather than a full design review, since this is v0.1 for
  internal use.

Tested with Streamlit's `AppTest` harness, including a 3-variant two-sided
scenario (one clear winner, one clear loser, one flat) that correctly
produced STOP_EFFICACY / STOP_SIGNIFICANT_LOSS / STOP_FUTILITY respectively,
one per variant, with no exceptions; and, this pass, the cadence calculator
(traffic entered → correct projected duration/interval; left at 0 → skip
message) and the early-stop novelty caution (confirmed it does NOT fire on
a late/normal-timing stop, and DOES fire — with the right information
fraction — on a stop engineered to happen before 50% information). Not the
same as a human clicking through it in a real browser, so give it a real
look before relying on it.

## Decision support added this pass

Going through every point in the tool where an analyst has to make a
judgment call (not just fill in a number), rather than leaving it silent:

- **Baseline / standard deviation** — help text on the Design page nudging
  toward a representative pre-test window (4-6 weeks) rather than a single
  day or a promo/holiday-skewed period, and (for continuous metrics) a
  flag that outlier-heavy metrics inflate required sample size a lot and
  may be worth capping or swapping for a more robust metric.
- **MDE** — reframed as "the smallest effect that would change a decision",
  with the quadratic sample-size cost of shrinking it spelled out, since an
  unrealistically small MDE is probably the single most common way a test
  design ends up needing far more traffic than the business actually has.
- **Alpha / power** — framed around decision stakes (tighter alpha for
  expensive/irreversible rollouts, higher power when missing a real winner
  is costly) rather than presented as bare statistical knobs.
- **Number of looks** — explains that, under O'Brien-Fleming spending, more
  looks cost almost no extra sample size (the early boundaries are barely
  reachable anyway) but buy more chances to stop early — so there's little
  reason to under-plan this.
- **Futility** — when it's worth it (frees up traffic on doomed tests) vs.
  when to skip it (if there's a separate reason to always run to the full
  planned sample).
- **Check-in cadence** (the feature that prompted this pass) — translates
  the abstract "n_looks spread across information fraction" into a
  concrete calendar suggestion, given expected traffic: `n_max_per_arm`
  and `n_looks` and traffic together imply both how long the test will
  take and how often a check actually adds new information. Cadence
  should scale with a test's own traffic, not follow one fixed daily/weekly
  rule for every test — a high-traffic test and a low-traffic one on the
  same `n_looks` plan want very different calendars. Also nudges against
  checking well before ~10% information (O'Brien-Fleming makes an early
  look nearly un-actionable) and against checking much more often than
  planned (doesn't break error control, since boundaries are recomputed
  at the actual observed information fraction either way, but erodes the
  sample-size efficiency the design was calibrated for and invites
  informal peeking between formal looks).
- **Monitor-page cautions** — novelty/day-of-week effects (statistically
  valid early stops can still be riding a transient effect — a business
  consideration on top of the statistics, not one the design enforces),
  and a restated reminder of the equal-traffic-allocation assumption
  behind the observed information fraction.

## Conventions found in your existing Streamlit setup (internal-experf-dashboard)

Looked at your `internal-experf-dashboard` repo (the CRO performance
dashboard) for conventions to match:
- App lives in its own subfolder (e.g. `cro-dashboard/`) with `.streamlit/`,
  a `data_sources/` folder (one module per external integration --
  `harvest.py`, `jira.py`, `monday_com.py`, `trello.py`), `app.py`,
  `config.py`, `requirements.txt`, `DEPLOY.md`.
- Secrets: a committed `.streamlit/secrets.toml.example` documenting the
  shape, real `secrets.toml` git-ignored, one `[service_name]` TOML table
  per integration, deployed to `/opt/apps/<name>/repo/cro-dashboard/.streamlit/secrets.toml`
  on the server (mode 600).
- Deployment: EC2 via AWS SSM (no direct SSH), a systemd service
  (`systemctl restart <name>`), Python venv at `/opt/apps/<name>/venv`,
  GitHub Actions auto-deploy via OIDC. Hosted at `<name>.120feet.com`,
  gated by **Cloudflare Access** for auth (matches what you said -- this
  confirms the mechanism: Cloudflare sits in front and gates the request
  before it reaches the app, so the app itself needs no login code).
- Their `app.py` is a single ~1,400-line file rather than Streamlit's
  native `pages/` folder. This app uses `pages/` instead (more idiomatic,
  easier to navigate) -- flag if you'd rather I collapse it into one file
  to match exactly.

Not yet done: actually restructuring this into a matching subfolder
(`sequential-testing/` or similar) with a `DEPLOY.md`/`secrets.toml.example`
of its own, since I don't yet know whether this goes in the same repo as
`internal-experf-dashboard` or a new one -- see open questions.

## Scope decisions made to keep this buildable

- **Non-binding futility** — the efficacy boundary is calibrated ignoring the
  futility rule, and vice versa. Standard, most common convention. Means: if
  analysts always follow the futility stop, TRUE Type I error and power end
  up slightly *more conservative* than nominal — see "A real finding" below.
- **Two-sided implementation**: the lower ("significantly worse") boundary
  is the exact mirror of the upper boundary (negated), which holds exactly
  by symmetry under the null -- no separate integration needed. Power is
  still calibrated toward detecting an effect in the hypothesized ("wins")
  direction, standard practice for two-sided sample-size formulas generally.
- **Multiple variants**: handled via **Bonferroni correction** (alpha
  divided by the number of variants) -- simple and always valid, but
  conservative: it doesn't model the (real, and favourable) correlation
  between comparisons that share a control arm. Validated via Monte Carlo
  with that realistic correlation modeled (see below) -- the true
  family-wise error came out well under the nominal budget, as expected.
  A less-conservative Dunnett-style joint calculation is possible later if
  Bonferroni's conservatism becomes a real cost (bigger required sample
  size than strictly necessary).
- **Count/ratio metrics** still route through the continuous (mean-based)
  path, which assumes approximately normal per-user values — fine for
  something like "items per order", maybe not for a zero-inflated metric.
- **Interim information fraction** is computed from `min(control_n,
  variant_n) / n_max_per_arm` per comparison — assumes roughly equal
  allocation between arms. Flag if traffic splits are meaningfully unequal.
- **Persistence is now built (`engine/store.py`), but optional and
  host-dependent.** A design can be named and saved on the Design page
  (SQLite, one row per design + one row per look x arm for interim data);
  saved designs show up in a picker there and in a "recently saved" list on
  Home, and the Monitor page gets a "Save entries" button once a design is
  saved. An unsaved design still behaves exactly as before — everything
  lives only in that browser tab. Critically, this only survives app
  restarts if the host gives the app a persistent disk — **not** true of
  Streamlit Community Cloud (wiped on every redeploy and sleep/wake cycle),
  which is where this has been tested so far. Point the `GSD_DB_PATH`
  environment variable at wherever a persistent volume lives once this
  moves to the planned EC2 + systemd host; see `store.py`'s docstring.

## What's NOT built yet

- The **always-valid / mSPRT alternative method** — this engine only
  implements the GSTEF/AGILE side so far.
- **Bias-corrected point estimates, confidence intervals, and p-values** at
  the point of stopping — the Monitor page currently shows the NAIVE
  fixed-horizon estimate/CI, known to be slightly optimistic once you've
  been peeking. Fine for a running readout, not yet fine as a final
  client-facing effect-size claim.
- **Adobe Analytics/Target connectors** (manual entry only, per your
  instruction) and any test-history storage (also per your instruction, for
  now).
- Matching your actual repo/deployment layout exactly (see conventions
  section above and open questions).

## Validation status

Five independent checks, all passing (`python3 -m engine.validate`):

1. **Self-consistency** — re-running the recursive integration on the solved
   boundaries reproduces the target alpha/beta spend to ~1e-12.
2. **Monte Carlo simulation** — an independent numerical method (300,000
   simulated sample paths, no recursive integration involved) confirms the
   solved boundaries achieve the target Type I error, for K=3, 4, and 5.
3. **End-to-end design check** — a full binary-metric design (6 looks,
   O'Brien-Fleming efficacy + futility) simulated 300,000 times under H0 and
   H1, using the complete stopping rule.
4. **Two-sided check** — confirms P(cross either boundary) under H0 hits the
   family-wise alpha, the two boundaries are symmetric, and power holds for
   the hypothesized direction (300,000 sims: 4.93% vs 5% target, symmetric,
   79.97% vs 80% target power).
5. **Multi-variant Bonferroni check** — 3 variants vs. a shared control,
   simulated with the REALISTIC correlation induced by sharing one control
   arm (independent per-arm Brownian processes -- gives the textbook 0.5
   pairwise correlation between comparisons, matching Dunnett's-test
   theory). Confirms each variant's marginal false-positive rate hits its
   Bonferroni-adjusted target, family-wise error stays under the nominal
   budget (4.28% vs. 5%, conservative as expected), and a true winner among
   3 null variants still achieves its target power off its own boundary.

**What this hasn't been checked against**: a second reference implementation
(R's `rpact`/`gsDesign` — no CRAN access from this sandbox). Given this
drives real client stop/go decisions, treat that cross-check as a hard
prerequisite before going live.

### A real finding from validation (not a bug, but worth knowing)

With both the efficacy and (non-binding) futility rules followed together:
achieved Type I error came out at **4.49%** against a nominal 5%, and
achieved power at **76.9%** against a nominal 80% (6-look example, O'Brien-
Fleming spending both sides). This ~3-point power shortfall is the expected,
documented cost of non-binding futility — some genuinely winning tests get
stopped early by the futility rule before they'd have crossed the efficacy
boundary. Worth deciding whether the Monitor/Design pages should surface an
"actual power if futility is followed" number alongside the nominal one.

## Try it

```
cd gsd-engine
pip install -r requirements.txt
python3 -m engine.validate          # run the validation suite
streamlit run app/Home.py           # launch the app locally
```

## Open questions before the next build phase

1. **Where this lives** — new repo, or a new subfolder alongside
   `cro-dashboard/` in `internal-experf-dashboard`? And should `app.py` be
   one file (matching `cro-dashboard/app.py`) or stay as the current
   `pages/`-based multipage structure?
2. **Framework default** — should GSTEF (AGILE) be the default framework
   with always-valid as an override, or does the analyst choose per test
   with no default? (Always-valid itself is still unbuilt either way.)
3. **Persistence is built (`engine/store.py`) but not yet durable in
   production** — it needs an actual persistent disk to survive restarts,
   which means getting this off Streamlit Community Cloud and onto the
   planned EC2 + systemd host (per the conventions above) before it's
   trustworthy for real use. Confirm `GSD_DB_PATH` and back-up strategy
   (it's a single file — copying it somewhere is the whole backup) once
   that move happens.
