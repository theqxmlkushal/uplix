# Uplix

### Causal Uplift & Policy Evaluation Engine

**Not just who will buy. Who will buy because of your intervention?**

Uplix is a causal machine learning project for estimating the incremental impact of marketing interventions, identifying customers who are most likely to respond to those interventions, and evaluating targeting strategies based on expected profit.

Traditional conversion models estimate who is likely to purchase. Uplix focuses on a different question: **would this customer be more likely to purchase because they received a coupon, email, advertisement, or another treatment?** The goal is to make targeting decisions using estimated treatment effects and measurable business value—not purchase probability alone.

> **Project status:** Uplix is being developed in phases. The capabilities below describe the project specification and development roadmap; do not interpret every item as already implemented.

---

## Table of Contents

- [Why Uplix?](#why-uplix)
- [Core Concepts](#core-concepts)
- [How It Works](#how-it-works)
- [Uplift Models](#uplift-models)
- [Evaluation Metrics](#evaluation-metrics)
- [Profit-Based Targeting](#profit-based-targeting)
- [Policy Evaluation](#policy-evaluation)
- [Validation Strategy](#validation-strategy)
- [Datasets](#datasets)
- [Planned Technology Stack](#planned-technology-stack)
- [Project Structure](#project-structure)
- [Development Roadmap](#development-roadmap)
- [Getting Started](#getting-started)
- [Guiding Principles](#guiding-principles)

## Why Uplix?

A customer who is already certain to purchase may not need a discount. Another customer may purchase only after receiving one. A third customer may actually become less likely to purchase after an intervention.

Treating all likely buyers alike can waste marketing spend and reduce profit. Uplix aims to distinguish between these responses and help answer:

- Which customers are likely to change their behaviour because of an intervention?
- Which customers should receive the treatment, and which should remain in the control group?
- Does a targeting policy create incremental value after discounts and treatment costs?
- How reliable is the estimated policy value, and how does it compare with simpler baselines?

## Core Concepts

For each user, the project works with:

- `X`: observed user features.
- `T`: binary treatment indicator (`1` = treated, `0` = control).
- `Y`: binary outcome (`1` = purchase or conversion, `0` = no purchase).
- `e`: treatment assignment probability. In the randomized experiment specified for this project, this propensity is known and estimated as the observed treatment rate, `T.mean()`.

The two potential outcome probabilities are:

- `mu1(x) = P(Y = 1 | X = x, T = 1)`
- `mu0(x) = P(Y = 1 | X = x, T = 0)`

The **Conditional Average Treatment Effect (CATE)** is estimated as:

\[
\tau(x) = \mu_1(x) - \mu_0(x)
\]

A positive value indicates that the treatment is estimated to increase the outcome probability for users with those features; a negative value indicates a potentially harmful effect.

The individual treatment effect cannot be directly observed for a single user because only one of that user's two potential outcomes is observed. Uplix therefore estimates treatment effects from groups of comparable users and evaluates its methods in a simulator where the true effects are known.

## How It Works

The intended workflow moves from controlled validation to real-world experimentation:

1. **Create a controlled environment.** Generate synthetic users with known treatment effects and randomized treatment assignment.
2. **Check the experiment.** Validate treatment randomization and feature balance before fitting uplift models.
3. **Estimate outcomes and treatment effects.** Train outcome models and compare T-Learner, X-Learner, and Doubly Robust Learner approaches.
4. **Rank users by estimated uplift.** Use uplift-oriented metrics to evaluate whether high-scoring users show larger incremental response.
5. **Make an economic decision.** Combine predicted treatment effects with purchase margin, discount, and treatment cost.
6. **Evaluate the policy.** Estimate the value of a targeting strategy using a doubly robust off-policy estimator on held-out evaluation data.
7. **Run experiments and inspect results.** Extend the workflow to real datasets, a service API, monitoring, and a dashboard as the project progresses.

## Uplift Models

Uplix is designed to compare multiple meta-learners. All learners expose a consistent prediction interface:

- `predict_tau(X)` — estimated treatment effect.
- `predict_mu0(X)` — estimated outcome probability under control.
- `predict_mu1(X)` — estimated outcome probability under treatment.

| Model | How it works |
|---|---|
| **T-Learner** | Trains one outcome model on treated users and another on control users. The estimated uplift is the difference between their predictions. |
| **X-Learner** | Uses imputed treatment effects from both groups, trains effect models, and combines their predictions using the treatment propensity. |
| **DR-Learner** | Builds a doubly robust pseudo-outcome using cross-fitted outcome predictions, then learns how that pseudo-outcome varies with user features. |
| **S-Learner (baseline)** | Trains one outcome model with treatment as an additional feature, then compares predictions with treatment set to `1` and `0`. |

The shared outcome models estimate `mu0` and `mu1`. For the DR-Learner, cross-fitting ensures that each row's out-of-fold outcome predictions are produced by models that did not train on that row.

## Evaluation Metrics

Uplix emphasizes metrics that assess treatment-effect ranking and estimation rather than ordinary classification performance alone.

| Metric | Purpose |
|---|---|
| **Qini curve and Qini coefficient** | Evaluate how effectively a model ranks users by incremental response. |
| **Uplift by decile** | Compare treated and control outcome rates within score-ranked groups. |
| **PEHE** | Measure treatment-effect estimation error when the true effects are available, such as in the synthetic simulator. |
| **Bootstrap confidence intervals** | Quantify uncertainty around reported metrics through repeated resampling. |

The Qini curve is built by sorting users by predicted uplift and comparing observed treated outcomes with the corresponding control-group estimate at each prefix of the ranked population. The Qini coefficient summarizes the area between this curve and its random-ranking reference line.

## Profit-Based Targeting

A high predicted uplift does not automatically mean that a treatment is profitable. Uplix includes an economic decision layer that accounts for purchase margin, discount, and treatment cost.

Let:

- `m` = profit from a purchase before discount.
- `d` = discount paid when a treated user purchases.
- `c` = cost of sending the treatment.

The expected rewards are:

\[
M_1(x) = (m-d)\mu_1(x)-c
\]

\[
M_0(x) = m\mu_0(x)
\]

The expected incremental gain from treating a user is:

\[
\operatorname{gain}(x) = M_1(x)-M_0(x)
= m\tau(x)-d\mu_1(x)-c
\]

The decision rule is to treat when:

\[
\operatorname{gain}(x) > 0
\]

This distinction is central to Uplix: the objective is not simply to maximize conversions or predicted uplift, but to identify interventions expected to create positive incremental profit.

For a logged observation, realized reward is defined as:

\[
R = Y(m-dT)-cT
\]

## Policy Evaluation

A targeting policy maps user features to a treatment decision, `pi(x) in {0, 1}`. Uplix's specification uses a **doubly robust off-policy estimator** to estimate the policy's expected reward on held-out evaluation data.

For each evaluation row, the estimator combines:

1. The outcome model's predicted reward under the action selected by the policy.
2. An inverse-propensity correction when the observed treatment matches the policy's selected action.

The estimated policy value is the mean of these doubly robust scores. The project also reports a standard error and a 95% confidence interval, and compares two policies using paired score differences.

**Evaluation separation matters:** outcome models are fitted on training data only. Evaluation rows must not be used to train those models. The treatment propensity is known from the randomized assignment design; the correction is not intended to hide confounding or repair an invalid experiment.

## Validation Strategy

Uplix uses tests as a core part of the project rather than as a final checkbox. The synthetic simulator provides known ground-truth outcome probabilities and treatment effects, making it possible to test whether the implementation recovers what it should.

The planned test suite covers:

- Economic formulas and input validation.
- Reproducible synthetic data generation and valid probability ranges.
- Representation of persuadable, sure-thing, lost-cause, and sleeping-dog segments.
- Randomization checks using standardized mean differences (SMD).
- Hand-computed Qini examples and agreement with a slower reference implementation.
- Learner recovery against known synthetic treatment effects.
- Cross-fitting and doubly robust pseudo-outcome behaviour.
- Policy-value estimation, uncertainty intervals, placebo checks, and evaluation coverage.
- API contracts, data schemas, end-to-end behaviour, and deployment smoke tests as later phases are built.

Tests should not be weakened or modified merely to make an implementation pass. A failing test should first be understood, reproduced, and explained.

## Datasets

### Synthetic data

The initial simulator generates five user features from independent latent variables:

- `recency_days`
- `orders_30d`
- `coupon_use_rate`
- `basket_value`
- `tenure_months`

It generates known control and treatment outcome probabilities, assigns treatment using a Bernoulli randomized experiment, and samples the observed outcome from the probability corresponding to the assigned treatment. Ground-truth columns are retained for simulation checks and must never be supplied as model features.

### Real-world data

The roadmap names the following datasets for later experiments:

- **Hillstrom**
- **Criteo**

Real-world uplift signals may be modest and estimates may have wide confidence intervals. Results should be reported with uncertainty rather than presented as guaranteed gains.

## Planned Technology Stack

| Area | Planned tools |
|---|---|
| Language | Python 3.11+ |
| Data handling | NumPy, Pandas |
| Machine learning | Scikit-learn, LightGBM, SciPy |
| API and validation | FastAPI, Pydantic |
| Testing and code quality | Pytest, Ruff |
| Dashboard | React |
| Packaging and deployment | `pyproject.toml`, Make, Docker, GitHub Actions |

The project specification keeps the core implementation deliberately straightforward: plain functions, type hints, reproducible random seeds, focused modules, and limited dependencies.

## Project Structure

The planned repository structure is:

```text
uplix/
├── pyproject.toml
├── Makefile
├── README.md
├── config.yaml
├── src/
│   └── persuadable/
│       ├── __init__.py
│       ├── economics.py
│       ├── simulator.py
│       ├── checks.py
│       ├── models.py
│       ├── learners.py
│       ├── metrics.py
│       ├── policy.py
│       ├── evaluate.py
│       ├── data.py
│       ├── experiments.py
│       ├── api.py
│       └── monitoring.py
├── tests/
├── docs/
│   └── EXPLAIN_<module>.md
├── results/
├── web/
├── Dockerfile
└── .github/
    └── workflows/
        └── ci.yml
```

The structure is a target layout from the project specification and may change as implementation decisions are made.

## Development Roadmap

Uplix is organized into seven gated phases. Each phase must pass its tests before work proceeds to the next phase.

| Phase | Focus | Completion gate |
|---|---|---|
| **1. Foundations** | Repository scaffold, economics, synthetic simulator, and randomization checks | Simulator and validation tests pass |
| **2. Uplift models** | Outcome models, T-/X-/DR-Learners, and ranking metrics | Metric correctness and learner recovery tests pass |
| **3. Policy evaluation** | Decision rule, doubly robust policy evaluation, baselines, placebo and coverage tests | Unbiasedness, coverage, and placebo tests pass |
| **4. Real-data experiments** | Hillstrom and Criteo experiments, JSON exports, and automatic verdicts | End-to-end and schema tests pass |
| **5. API and monitoring** | FastAPI service, monitoring, and permanent holdout | API contract and golden-number tests pass |
| **6. Dashboard** | Animated React dashboard | Parity, end-to-end, accessibility, and performance tests pass |
| **7. Deployment** | Docker, CI/CD, cloud deployment, and demo workflow | Smoke tests pass on the live deployment |

## Getting Started

The project specification targets Python 3.11 or later and defines `install`, `test`, `test-slow`, and `lint` Makefile targets. The exact setup and run commands will be added here once the repository scaffold and executable entry points are in place.

## Guiding Principles

- **Causality over correlation:** estimate incremental treatment impact, not just outcome probability.
- **Profit over vanity metrics:** account for discounts and intervention costs.
- **Honest evaluation:** report confidence intervals and acknowledge weak or uncertain uplift signals.
- **No data leakage:** keep true simulator effects and evaluation-only data out of model training.
- **Reproducibility:** use fixed random seeds and deterministic tests wherever possible.
- **Understandable engineering:** keep code modular, documented, and explainable—not just generated and shipped.

---

**Uplix — Predict the impact. Maximize the lift.**
