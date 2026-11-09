# Recovery advisor

The advisor diagnoses each new case and recommends one action. It is advice only: the deterministic policy engine decides whether the action may run, and the Tool Gateway executes it. No output of the advisor can move money, change an amount or skip a policy check.

```
financial truth (PostgreSQL) ─▶ risk engine (deterministic) ─▶ advisor (recommends) ─▶ policy (decides) ─▶ Tool Gateway (acts)
```

## The graph

The advisor is a LangGraph state graph (`infrastructure/ai/recovery_graph.py`):

```
load_context ─▶ risk_analysis ─▶ diagnosis_and_strategy ─▶ validate ─┬─▶ END              (valid decision)
                                        ▲                            ├─▶ retry once        (invalid answer)
                                        └────────────────────────────┘
                                                                     └─▶ safe_failure ─▶ END (second invalid answer,
                                                                                              or the model unavailable)
```

`diagnosis_and_strategy` asks a **reasoner**:

| `LLM_PROVIDER` | Reasoner | `advisor_model` recorded |
|---|---|---|
| `rules` (default) | RecoverX's deterministic rules: event type, reliability, repeat failures, overdue days, and what has recovered this customer before | `langgraph-v1` |
| `anthropic` | Claude (`LLM_MODEL`, default `claude-opus-5-5`) with structured output | the model id |

Whatever the reasoner returns is validated against `RecoveryDecision` (`domain/decisions.py`): required fields, `recommended_action` from the controlled vocabulary, `confidence` and `recovery_probability` between 0 and 1. An invalid answer is retried once, with the validation error as feedback. A second invalid answer, a timeout, a rate limit, a provider error or a safety refusal ends in a **safe failure**: `ESCALATE_TO_HUMAN` at confidence 0 with root cause `ADVISOR_OUTPUT_INVALID`. Zero confidence is below any merchant's floor, so policy always sends the case to a person, with escalation reason `INVALID_AI_OUTPUT` (the *low confidence* review queue). Nothing is executed. The audit trail records `ai.invalid_output_safe_failure` with the reason.

## What the advisor sees

The canonical case context built from RecoverX's own records (`case_facts` in `claude_reasoner.py`): the revenue event type, the amount at risk, the risk engine's score, probability and expected recovery value, days overdue, repeat failures, and the customer's reliability, payment counts, lifetime value, recovery history, preferred channel and language. It never sees raw Razorpay payloads, and Claude is not sent the customer's name, email or phone number.

## What happens with the recommendation

| Recommendation | Policy auto-approves | Otherwise |
|---|---|---|
| `PAYMENT_LINK`, `PAYMENT_RETRY`, `REMINDER`, `PERSONALIZED_OUTREACH`, `CHECKOUT_RECOVERY`, `FOLLOW_UP` | The Tool Gateway executes it. If the merchant switched the action off, the case is escalated (`ACTION_NOT_ALLOWED`) | Human review or stop, per policy |
| `MONITOR`, `NO_ACTION` | Nothing runs; the case stays open until paid or expired (`workflow.monitoring`) | Human review or stop |
| `ESCALATE_TO_HUMAN`, `STOP`, `PROMISE_TO_PAY` | A person decides (`ADVISOR_RECOMMENDS_REVIEW`) | Human review or stop |

The policy engine reads the advisor's confidence and nothing else from it. Amounts, the risk score and the expected recovery value always come from the risk engine.

## Claude configuration

Set `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY`. Each new case is one Messages API request:

- `output_config.format` with a JSON schema mirroring `RecoveryDecision` (the action is an `enum`), and `output_config.effort` from `LLM_EFFORT` (default `low`). Thinking is left to the model; Claude Opus 5.5 always thinks adaptively.
- `fallbacks: "default"` with the `server-side-fallback-2026-07-01` beta, so a request the model declines on safety grounds is re-run on a fallback model within the same call. The decision's reasoning notes when a fallback model answered. Set `LLM_REFUSAL_FALLBACK=false` to turn it off, for example on a platform other than the Claude API.
- `LLM_TIMEOUT_SECONDS` (20) and `LLM_MAX_RETRIES` (1) bound the wait.

**Latency.** The advisor runs while the webhook that opened the case is being processed, so a slow model response delays the webhook's reply. Razorpay retries deliveries that time out, and RecoverX's event-id deduplication keeps a retry from opening a second case, but keep `LLM_EFFORT` low and the timeout short.

**Cost.** Each new case is one request, plus one retry when the first answer is invalid. A batch evaluation (`POST /demo/run-batch`) replays every case through the configured advisor, so with Claude configured it makes one request per case.

**Verification.** The request and response handling are tested through the real Anthropic SDK against a mock HTTP transport (`tests/unit/test_advisor_graph.py`): request shape, beta header, schema, the absence of personal data, invalid JSON, rate limits, overload, refusals and fallbacks. No live Claude call is made by the test suite.

The Integrations page shows the advisor as *Rules only*, *Claude* (configured) or *No credentials*.

## Learning from outcomes

Every case that closes updates its customer's recovery profile (`application/learning.py`). A recovery or an expiry adjusts `historical_recovery_rate` and `previous_recovery_attempts`. A recovery also sets `best_historical_recovery_action` to the executed action found in most of the customer's recovered cases. A payment that arrives after expiry turns the counted loss into a recovery rather than counting the case twice. The next case for that customer sees the new profile in the risk engine's recovery probability and in the advisor's strategy: the rules prefer a link-based action that has recovered this customer before when their recovery rate is 60% or more. The customer page shows the learned figures, and each update is audited as `customer.profile_learned`.

## Evaluation

`POST /demo/run-batch` (or `python -m scripts.run_batch_evaluation`) replays every case through the configured advisor and the policy engine. It reports invalid advisor decisions (including safe failures), policy violations, false escalations and the financial outcomes, and records them as `evaluation.completed`. Each case is judged on the inputs policy decided with (the days overdue recorded at intake), and an action a reviewer approved is not a violation: a person approving what policy sent to review is policy working. The Command Center shows the latest run.

## Failure lab

`POST /simulation/invalid-ai` runs the real graph with a reasoner that returns malformed output; validation rejects it twice and the case fails safe. `POST /simulation/low-confidence` drops the advisor's confidence below the floor and lets the policy engine escalate. Neither sets the outcome directly.
