# Group sequential testing engine — v0.3 (core + Streamlit app)

First-pass implementation of 120F's group sequential testing tool, following
the AGILE-style method referenced in the project brief (analytics-toolkit.com
/ Georgi Georgiev): a Group Sequential Test with Efficacy and Futility
boundaries (GSTEF), built on Kim-DeMets/Lan-DeMets error-spending functions,
O'Brien-Fleming as the default spending function, non-binding futility.

Now supports one-sided or two-sided testing and more than one variant vs. a
shared control (Bonferroni-adjusted), both as user choices in the Design
page — on top of the Streamlit app added last pass.

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
  expected N under H0/H1. Binary and continuous metrics. **New**: `sides`
  ("one"/"two") and `n_variants` (Bonferroni-adjusted) as first-class inputs.
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
- `pages/1_Design_a_Test.py` — the design calculator as a form. **New**:
  Sides (one/two) and Number of variants inputs; the boundary table/chart
  show the lower ("significant loss") boundary when two-sided.
- `pages/2_Monitor_a_Test.py` — manual interim data entry, one row per look,
  with a column pair per variant when more than one is in the design →
  a decision + trajectory chart PER VARIANT. Manual entry is deliberate (no
  Adobe server-to-server credentials yet) — this page is where an automated
  "pull latest from Adobe" button would slot in later, without engine/
  changing. **New**: a prominent "nothing on this page is saved" banner —
  there's no storage layer (see open questions), so re-entering data on
  every visit is the expected workflow for now, not a bug.
- `.streamlit/config.toml` + `app/_theme.py` — light 120F brand pass (brand
  orange primary, warm off-white surfaces, Manrope/Archivo Black/JetBrains
  Mono type). Kept to the "quick/internal tool" end of the brand skill's
  guidance rather than a full design review, since this is v0.1 for
  internal use.

Tested with Streamlit's `AppTest` harness, including a 3-variant two-sided
scenario (one clear winner, one clear loser, one flat) that correctly
produced STOP_EFFICACY / STOP_SIGNIFICANT_LOSS / STOP_FUTILITY respectively,
one per variant, with no exceptions. Not the same as a human clicking
through it in a real browser, so give it a real look before relying on it.

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
- **No persistence, by your instruction** — nothing saves between sessions;
  users re-enter data each visit, and the app tells them so clearly (banners
  on Home and Monitor).

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
3. **Persistence, when it's time** — even a lightweight SQLite file would
   need to know whether your hosting (EC2 + systemd, per the conventions
   above) keeps a persistent disk across deploys, which it likely does
   (unlike, say, Streamlit Community Cloud) -- worth confirming when this
   becomes a priority.
