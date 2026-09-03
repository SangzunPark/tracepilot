# TracePilot Case Specifications — INC-001 to INC-012

Revision 3. Finalized after review of INC-004, INC-006, INC-012, metric ownership,
cross-case consistency, and wrong-hypothesis evaluation semantics.

World: `checkout-api`, `billing-api`, `authentication-service`, `postgresql`,
`payment-gateway`

Dependencies:

- checkout-api → authentication-service, postgresql, payment-gateway
- billing-api → postgresql, payment-gateway
- authentication-service → postgresql

All times UTC. Each case uses a different date so timelines stay independent.

---

## Metric ownership convention

This convention applies to every case. INC-001 must be updated to match.

```
Application services (checkout-api, billing-api, authentication-service)
    db_connection_pool_usage      percent   client-side pool the app borrows from
    http_5xx_rate                 percent
    request_latency_p95           ms
    memory_usage_percent          percent
    (case-specific rates as needed)

PostgreSQL
    active_connections            count     server-side connection count
    query_latency_p95             ms

payment-gateway
    response_latency_p95          ms

```

The distinction matters: a saturated *client* pool in one service while another
service's pool is healthy is what localises the fault. Attributing pool usage to
`postgresql` would erase that signal.

### Required change to INC-001

In `evaluation/cases/INC-001/metrics.json`, for `metric-db-pool-001`,
`metric-db-pool-002`, `metric-db-pool-003`:

```
service:  "postgresql"            ->  "checkout-api"
metric:   "connection_pool_usage" ->  "db_connection_pool_usage"

```

Leave `source_id` values unchanged so `ground_truth/INC-001.json` needs no edit.
`metric-db-connections-*` stays on `postgresql` as `active_connections`.

---

## Simple single-cause (INC-001 to INC-008)

---

### INC-001 — DB connection-pool exhaustion (already built)

```
case_id:    INC-001
category:   simple_single_cause
root_cause: db_connection_pool_exhaustion
service:    checkout-api
date:       2026-01-12
started_at: 14:10

```

Existing fixture. Only the metric ownership change above is required.

Required evidence: `metric-db-pool-003` (temporal), `log-checkout-db-timeout-001`
(mechanistic). Distractor: v2.7.4 deployment at 13:40, refuted by healthy logs
through 14:01.

---

### INC-002 — Faulty deployment

```
case_id:    INC-002
category:   simple_single_cause
root_cause: faulty_deployment
service:    checkout-api
date:       2026-01-19
started_at: 14:02

```

**Timeline**

```
13:30  checkout-api healthy, http_5xx_rate 0.2%
13:45  postgresql query_latency_p95 120ms -> 180ms
14:00  checkout-api v3.1.0 deployed
14:02  checkout-api errors begin
14:05  http_5xx_rate 9.4%
14:10  http_5xx_rate 14.1%

```

**Temporal evidence** — deployment event at 14:00, two minutes before onset.

**Mechanistic evidence** — error log naming the code path introduced in v3.1.0.
Suggested: `Unhandled response shape from inventory lookup (build 3.1.0)`

**Required evidence** — deployment + log.

**Distractor** — postgresql query latency rose at 13:45.

**Counter-evidence** — the latency rise began 15 minutes *before* the deployment
and never worsened; checkout-api ran clean through that window;
checkout-api `db_connection_pool_usage` stays near 40% throughout.

**leakage\_aliases**

```
faulty deployment
bad deployment
deployment regression

```

**Why it is not trivial** — a deployment alone does not prove causation. INC-001
also has a deployment and it is not the cause. The version-specific error plus the
clean pre-deploy window are what separate them.

---

### INC-003 — Feature-flag misconfiguration (boolean)

```
case_id:    INC-003
category:   simple_single_cause
root_cause: feature_flag_misconfiguration
service:    checkout-api
date:       2026-01-26
started_at: 15:22

```

**Timeline**

```
11:00  billing-api v2.4.0 deployed (unrelated service)
15:00  checkout-api healthy
15:20  flag "checkout_express_path" changed false -> true
15:22  checkout-api errors begin
15:30  http_5xx_rate 22.7%

```

**Temporal evidence** — flag change at 15:20.

**Mechanistic evidence** — error from the newly enabled path.
Suggested: `Express checkout path returned null shipping quote`

**Required evidence** — feature flag + log.

**Distractor** — the billing-api deployment earlier that day.

**Counter-evidence** — four hours earlier, different service; billing-api metrics
stay normal; checkout-api has no deployment on this date at all.

**leakage\_aliases**

```
feature flag misconfiguration
misconfigured feature flag
feature flag caused

```

**Why it is not trivial** — flag history alone shows a change, not that the change
broke anything. Flags change routinely.

---

### INC-004 — Database credential failure

```
case_id:    INC-004
category:   simple_single_cause
root_cause: database_credential_failure
service:    authentication-service
date:       2026-02-02
started_at: 09:01

```

Root cause label revised from `expired_database_credential`. The observable
evidence establishes that the database is rejecting this service's credentials; it
cannot distinguish expiry from revocation, rotation, or a wrong secret. The ground
truth must not claim more than the evidence supports.

**Timeline**

```
08:00  authentication-service healthy, login_success_rate 99.8%
08:45  postgresql active_connections normal (38)
09:00  authentication-service db_auth_failure_rate 0% -> 100%
09:01  login_success_rate drops to 4%
09:05  checkout-api reports auth dependency failures

```

**Temporal evidence** — `db_auth_failure_rate` on authentication-service goes
0% -> 100% at 09:00, one minute before onset.

**Mechanistic evidence** — the rejection log.
Suggested: `password authentication failed for user "auth_service"`

**Required evidence** — metric + log.

**Distractor** — checkout-api errors appear too, so checkout looks like the problem.

**Counter-evidence** — checkout-api's own `db_connection_pool_usage` is normal;
postgresql `active_connections` is normal; only authentication-service is being
rejected; no deployment or flag change anywhere that morning.

**leakage\_aliases**

```
database credential failure
credential failure
database authentication failure

```

**Why it is not trivial** — the symptom surfaces in checkout-api but the cause is
one hop away. The agent has to use the dependency graph.

**Note** — the abrupt all-or-nothing switch with no accompanying config change is
what rules out a code or load problem. It does not identify *why* the credential
stopped working, and the root cause label no longer claims that it does.

---

### INC-005 — External gateway failure

```
case_id:    INC-005
category:   simple_single_cause
root_cause: external_gateway_failure
service:    checkout-api
date:       2026-02-09
started_at: 11:16

```

**Timeline**

```
08:15  flag "checkout_retry_backoff" changed 2s -> 3s
11:00  checkout-api healthy
11:14  payment-gateway status operational -> degraded
11:15  payment-gateway response_latency_p95 240ms -> 7100ms
11:16  checkout-api payment timeouts begin
11:25  http_5xx_rate 31.2%
11:52  payment-gateway status degraded -> operational

```

**Temporal evidence** — external status change at 11:14.

**Mechanistic evidence** — the timeout log.
Suggested: `Payment authorization request timed out after 8000ms`

**Required evidence** — external status + log.

**Distractor** — the retry backoff flag change earlier in the day.

**Counter-evidence** — the flag changed at 08:15 and checkout-api ran clean for
three hours afterwards; postgresql metrics normal throughout.

**leakage\_aliases**

```
external gateway failure
payment gateway outage
gateway failure

```

**Why it is not trivial** — external status alone shows degradation, not that
checkout-api was affected by it. The timeout log ties them together.

---

### INC-006 — Application memory regression

```
case_id:    INC-006
category:   simple_single_cause
root_cause: application_memory_regression
service:    billing-api
date:       2026-02-16
started_at: 09:45

```

Required evidence expanded to three sources. Calling this a *regression* rather
than plain memory exhaustion requires tying the growth to a specific release, so
the deployment event is required, not optional.

**Timeline**

```
08:00  billing-api v4.2.0 deployed
08:05  billing-api healthy, memory_usage_percent 41%
08:30  memory_usage_percent 58%
09:00  memory_usage_percent 72%
09:30  memory_usage_percent 89%
09:40  postgresql active_connections rise slightly
09:45  billing-api worker restarts begin, requests fail
10:00  http_5xx_rate 17.9%

```

**Temporal evidence** — deployment at 08:00 **and** the rising memory metric.

**Mechanistic evidence** — the restart log.
Suggested: `Worker terminated: heap limit exceeded, restarting`

**Required evidence** — deployment + metric trend + log. Three sources.

**Distractor** — postgresql active\_connections rising at 09:40.

**Counter-evidence** — the DB rise starts *after* memory is already at 89%;
billing-api `db_connection_pool_usage` never exceeds 60%. The DB is downstream.

**leakage\_aliases**

```
memory regression
memory leak
application regression

```

**Why it is not trivial** — unlike INC-002, errors begin 105 minutes after the
deployment. "Did anything deploy right before?" finds nothing close. The agent has
to notice the gradual trend and connect it back.

---

### INC-007 — DB pool exhaustion in a dependency

```
case_id:    INC-007
category:   simple_single_cause
root_cause: db_connection_pool_exhaustion
service:    authentication-service
date:       2026-02-23
started_at: 16:40

```

**Timeline**

```
16:00  authentication-service healthy
16:10  authentication-service request rate begins climbing
16:25  postgresql active_connections 55 -> 78
16:34  authentication-service db_connection_pool_usage 100%
16:37  authentication-service logs connection acquisition timeouts
16:40  checkout-api login-dependent requests begin failing
16:50  checkout-api http_5xx_rate 26.4%

```

**Temporal evidence** — authentication-service `db_connection_pool_usage` reaches
100% at 16:34.

**Mechanistic evidence** — two logs form the chain:

- authentication-service: `Timed out acquiring database connection`
- checkout-api: `Authentication service call failed: upstream timeout`

**Required evidence** — metric + both logs.

**Distractor** — checkout-api is where users see failures, so checkout looks broken.

**Counter-evidence** — checkout-api's own `db_connection_pool_usage` stays near
45%; checkout-api has no deployment; payment-gateway is operational; failures are
confined to auth-dependent paths.

**leakage\_aliases**

```
connection pool exhaustion
db connection pool exhaustion
pool exhaustion

```

**Why it is not trivial** — same root cause as INC-001 but the agent must traverse
checkout-api → authentication-service → postgresql rather than stopping at
checkout-api → postgresql. This is where the per-service pool metric earns its
keep: one pool is saturated, the other is not.

---

### INC-008 — Feature-flag misconfiguration (numeric)

```
case_id:    INC-008
category:   simple_single_cause
root_cause: feature_flag_misconfiguration
service:    billing-api
date:       2026-03-02
started_at: 16:45

```

**Timeline**

```
16:00  billing-api healthy, invoice batch p95 340ms
16:40  flag "billing_invoice_batch_size" changed 50 -> 5000
16:42  billing-api request_latency_p95 340ms -> 9200ms
16:43  postgresql query_latency_p95 rises
16:45  billing-api batch jobs begin timing out
16:55  http_5xx_rate 41.0%

```

**Temporal evidence** — flag value change at 16:40.

**Mechanistic evidence** — the timeout log.
Suggested: `Invoice batch job exceeded 10000ms execution limit`

**Required evidence** — feature flag + metric + log.

**Distractor** — postgresql query latency rising at 16:43.

**Counter-evidence** — the DB latency rise starts *after* billing latency already
spiked; billing-api `db_connection_pool_usage` stays under 50%. The DB is reacting
to oversized batches, not causing them.

**leakage\_aliases**

```
feature flag misconfiguration
misconfigured feature flag
batch size misconfiguration

```

**Why it is not trivial** — different shape from INC-003: a numeric value change
producing a latency cascade rather than a boolean toggle producing instant errors.

---

## Wrong-initial-hypothesis (INC-009 to INC-012)

These four need a distinct structure:

```
early evidence  ->  reasonable but wrong hypothesis
later evidence  ->  that hypothesis breaks
full evidence   ->  actual root cause

```

---

### INC-009 — External gateway failure, deployment as distractor

```
case_id:    INC-009
category:   wrong_initial_hypothesis
root_cause: external_gateway_failure
service:    checkout-api
date:       2026-03-09
started_at: 14:07

```

**Timeline**

```
13:50  checkout-api v2.8.1 deployed
13:52  checkout-api request completed successfully
13:58  checkout-api request completed successfully
14:02  checkout-api request completed successfully
14:03  payment-gateway status operational -> degraded
14:04  payment-gateway response_latency_p95 240ms -> 7100ms
14:05  checkout-api payment timeout logs begin
14:07  http_5xx_rate 19.3%

```

**Wrong initial hypothesis** — the deployment 17 minutes before onset.

**What breaks it** — successful requests logged at 13:52, 13:58 and 14:02, all
after the deployment. And the errors are payment timeouts, not application errors.

**Temporal evidence** — external status degradation at 14:03.

**Mechanistic evidence** — checkout-api payment timeout log at 14:05.

**Required evidence** — external status + log.

**leakage\_aliases**

```
external gateway failure
payment gateway outage
gateway degradation caused

```

---

### INC-010 — DB pool exhaustion, external degradation as distractor

```
case_id:    INC-010
category:   wrong_initial_hypothesis
root_cause: db_connection_pool_exhaustion
service:    checkout-api
date:       2026-03-16
started_at: 10:40

```

**Timeline**

```
10:15  postgresql active_connections begin climbing (48 -> 62)
10:20  payment-gateway status operational -> degraded
10:24  checkout-api logs payment retries that succeed on retry
10:30  checkout-api db_connection_pool_usage 84%
10:35  payment-gateway status degraded -> operational
10:38  checkout-api db_connection_pool_usage 100%
10:40  checkout-api errors begin
10:50  http_5xx_rate 24.1%

```

**Wrong initial hypothesis** — the payment-gateway degradation 20 minutes earlier.

**What breaks it** — the gateway recovered at 10:35, five minutes *before* errors
began. The errors are database acquisition timeouts, not payment timeouts. And
the degradation only caused retries that succeeded.

**Temporal evidence** — `db_connection_pool_usage` reaches 100% at 10:38.

**Mechanistic evidence** — checkout-api DB acquisition timeout log at 10:40.

**Required evidence** — metric + log.

**leakage\_aliases**

```
connection pool exhaustion
db connection pool exhaustion
pool exhaustion

```

**Note** — the sharpest assumption-break in the suite. The distractor is not merely
correlated; it actually *resolved* before the incident started.

---

### INC-011 — Feature-flag misconfiguration, DB latency as distractor

```
case_id:    INC-011
category:   wrong_initial_hypothesis
root_cause: feature_flag_misconfiguration
service:    checkout-api
date:       2026-03-23
started_at: 09:52

```

**Timeline**

```
09:00  postgresql query_latency_p95 45ms -> 190ms (elevated, benign)
09:10  checkout-api request completed successfully
09:25  checkout-api request completed successfully
09:45  checkout-api request completed successfully
09:50  flag "checkout_inventory_precheck" changed false -> true
09:52  checkout-api errors begin
10:00  http_5xx_rate 28.6%

```

**Wrong initial hypothesis** — the elevated database latency starting at 09:00.

**What breaks it** — the latency was elevated for 52 minutes with zero errors, and
checkout-api `db_connection_pool_usage` stayed under 50% throughout. Errors align
to the minute with the flag change, and the error log names the precheck path.

**Temporal evidence** — flag change at 09:50.

**Mechanistic evidence** — `Inventory precheck returned unexpected status for region EU-WEST`

**Required evidence** — feature flag + log.

**leakage\_aliases**

```
feature flag misconfiguration
misconfigured feature flag
inventory precheck flag

```

---

### INC-012 — Payment gateway credential failure, flag change as distractor

```
case_id:    INC-012
category:   wrong_initial_hypothesis
root_cause: payment_gateway_credential_failure
service:    billing-api
date:       2026-03-30
started_at: 12:46

```

Root cause label revised from `expired_payment_gateway_credential`, for the same
reason as INC-004. HTTP 401 plus a healthy gateway establishes a client-side
authentication failure; it does not establish expiry specifically.

**Timeline**

```
12:30  flag "billing_retry_policy" changed "linear" -> "exponential"
12:32  billing-api invoice submitted successfully
12:38  billing-api invoice submitted successfully
12:44  billing-api invoice submitted successfully
12:45  payment-gateway status: operational
12:45  billing-api gateway_auth_rejection_rate 0% -> 100%
12:46  billing-api invoice submissions fail
12:55  invoice submission failure rate 100%

```

**Wrong initial hypothesis** — the retry policy flag change 16 minutes earlier.

**What breaks it** — successful invoice submissions logged at 12:32, 12:38 and
12:44, all after the flag change. The failures are HTTP 401, which a retry policy
cannot produce. And payment-gateway external status stays `operational` — the
gateway is healthy and rejecting *this client* specifically.

**Temporal evidence** — `gateway_auth_rejection_rate` on billing-api goes
0% -> 100% at 12:45.

**Mechanistic evidence** — `Payment gateway rejected request: HTTP 401 unauthorized (client billing-api)`

**Required evidence** — metric + log + external status (to rule out an outage).

**leakage\_aliases**

```
payment gateway credential failure
gateway credential failure
gateway authentication failure

```

**Note** — the pairing of "gateway operational" with "gateway rejecting us" is the
key signal. It rules out both the flag change and a gateway outage, leaving a
client-side authentication problem. That is exactly as specific as the root cause
label now claims.

---

## Cross-case checks

Verify these once all twelve exist.

### No case is solvable from a single tool

| Case Minimum sources  |                                  |
| --------------------- | -------------------------------- |
| INC-001               | metrics + logs                   |
| INC-002               | deployments + logs               |
| INC-003               | feature flags + logs             |
| INC-004               | metrics + logs                   |
| INC-005               | external status + logs           |
| INC-006               | deployments + metrics + logs     |
| INC-007               | metrics + logs from two services |
| INC-008               | feature flags + metrics + logs   |
| INC-009               | external status + logs           |
| INC-010               | metrics + logs                   |
| INC-011               | feature flags + logs             |
| INC-012               | metrics + logs + external status |

### Every wrong-hypothesis case has explicit evidence that breaks the distractor

```
INC-009  post-deployment healthy request logs (13:52, 13:58, 14:02)
INC-011  post-onset-of-latency healthy request logs (09:10, 09:25, 09:45)
INC-012  post-flag-change successful submissions (12:32, 12:38, 12:44)
INC-010  the distractor condition resolves at 10:35, before onset at 10:40

```

INC-010 works differently from the other three: the refutation is the gateway's
recovery, not a healthy-request record.

### Wrong-hypothesis replanning evaluation

Required-evidence satisfaction alone does not establish successful replanning.

For INC-009 through INC-012, evaluation must separately record whether the run:

1. formed or materially followed the intended distractor hypothesis, and
2. revised that hypothesis or investigation plan after receiving the explicit
   distractor-breaking evidence defined above.

The distractor-breaking observations are evaluation signals, not additional
runtime ground truth. Runtime completion logic must not read these evaluation
expectations or use them as an answer key.

### No observable record contains a leakage alias

Highest risk cases:

```
INC-004  avoid "credential failure", "authentication failure" as phrases;
         the log says "password authentication failed for user ..." which is a
         verbatim postgres error string -- check this against the alias list
INC-012  avoid "credential" anywhere in agent-visible data
INC-002  avoid "regression", "bad deploy" in log messages

```

INC-004 needs particular care: `database authentication failure` is on the alias
list while the mechanistic log contains `password authentication failed`. These are
different strings, so the verbatim check passes, but confirm this during review.
If it feels too close, narrow the alias list rather than weakening the log.

### Root cause distribution

Six families across seven concrete labels:

```
db_connection_pool_exhaustion         INC-001, INC-007, INC-010
feature_flag_misconfiguration         INC-003, INC-008, INC-011
external_gateway_failure              INC-005, INC-009
faulty_deployment                     INC-002
application_memory_regression         INC-006
credential failure family
    database_credential_failure       INC-004
    payment_gateway_credential_failure INC-012

```

### Distractor distribution

```
deployment          INC-001, INC-003, INC-009
database signal     INC-002, INC-006, INC-008, INC-011
feature flag        INC-005, INC-012
external status     INC-010
symptom location    INC-004, INC-007

```

No single distractor type dominates. This matters because an agent that learned
"always dismiss deployments" would score well for the wrong reason.

---

## Build order

```
1.  INC-001 metric ownership fix, then run the full domain test suite; all existing tests must remain green
2.  INC-002    simple, different root cause
3.  INC-009    first wrong-hypothesis case
    -> stop here and reassess timing
4.  INC-003, INC-005, INC-007    remaining distinct simple cases
5.  INC-004, INC-006, INC-008    the harder simple cases
6.  INC-010, INC-011, INC-012    remaining wrong-hypothesis cases

```

INC-009 is placed early on purpose. The wrong-hypothesis structure is different
enough from a simple case that meeting it once before building three more is
worth the reordering.