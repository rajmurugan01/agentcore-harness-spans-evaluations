# AgentCore Harness: spans and Evaluations, with and without CloudWatch Transaction Search

A runnable test of [this re:Post question](https://repost.aws/questions/QUjuUiA1FaSxCw22zhi2whyw): a Harness
created with `CfnHarness` has tracing switched on, yet every `StartBatchEvaluation` fails with "No spans were found
for the session". The asker's working theory was that the Harness never starts the Strands tracer. The AgentCore docs
say the opposite, that a harness emits traces automatically but that you must first
["enable Transaction Search in CloudWatch (one-time per account)"](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-operations.html).
This repo runs the same harness twice, once per setting, and records what happens.

## Result (one harness, one small model, one prompt, run twice)

| | Transaction Search OFF | Transaction Search ON |
|---|---|---|
| Trace segment destination | `XRay` | `CloudWatchLogs` |
| Spans delivered for the session | **0** | **5** |
| Span names | none | `invoke_agent Strands Agents`, `execute_event_loop_cycle`, `chat`, `chat <model>`, `POST /invocations` |
| `StartBatchEvaluation` (`Builtin.Helpfulness`) | **FAILED**: "No spans were found for the session" | **COMPLETED**, average score 0.83 |

The failure message in the OFF column is word for word the one in the question. Full output:
[docs/transcript.txt](docs/transcript.txt) (the whole A/B in one run) and
[docs/transcript-clean-baseline.txt](docs/transcript-clean-baseline.txt) (the baseline repeated with the destination
fully settled on `XRay`; the first run's baseline began while it was still `PENDING`, and the result was identical).

So in this test the Harness did emit spans, and the account setting decided whether they were delivered. The five spans
carry `gen_ai.*` attributes, a `session.id`, and parent and child IDs, so they are real spans, not log lines.
I did not find any Harness-side setting for this: `CfnHarness` has no observability property, as the asker said.

## Where the spans went

With Transaction Search on, the spans appeared in a `spans` log stream inside the harness's own runtime log group
(`/aws/bedrock-agentcore/runtimes/<runtime id>-DEFAULT`), next to an `otel-rt-logs` stream that holds logs and metrics.
In a manual check the account-wide `aws/spans` group existed but had no events. Batch evaluation worked when
pointed at the runtime log group alone. Pointing it at `aws/spans` while that group did not exist returned
"Log group 'aws/spans' not found in your account. Please verify the log group name and ensure your agent is emitting traces."

## Gotchas I hit

- **Enabling takes minutes.** The destination sat at `PENDING` for 366 seconds in the recorded run before turning `ACTIVE`.
  Invoke too early and you will see the OFF behaviour.
- **Switching back can take longer.** One revert stayed `PENDING` well past five minutes, then settled to `ACTIVE`
  by itself. The script now waits up to 25 minutes and deletes the resource policy only once the destination is `ACTIVE`.
- **The documented resource policy is easy to mangle in zsh.** I built the policy ARNs from `$ACCT:log-group...`; zsh read
  `:l` as a lowercase modifier and stored `<account-id>og-group`. `UpdateTraceSegmentDestination` then failed with
  "XRay does not have permission to call PutLogEvents on the aws/spans Log Group". Use `${ACCT}`. The policy used here is
  [docs/transaction-search-policy.json](docs/transaction-search-policy.json), after the
  [CloudWatch docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html).
- **Evaluation names allow only letters, digits and underscores** (`[a-zA-Z][a-zA-Z0-9_]{0,47}`), no hyphens.
- **Memory off is an empty object.** In `CfnHarness`, `memory: { disabled: {} }`, not `true`. Managed Memory is on by default and billed.
- **The newer CDK CLI did not resolve my SSO profile**, so the Makefile passes exported credentials.

## What I did not test

- Whether the asker's account already had Transaction Search on. They mention enabling "Tracing and Log Delivery" in the console, which may be a different setting.
  If theirs was on and their evaluations still fail, this test does not explain it.
- Their SDK, runtime and Harness versions, region, and model.
- Whether console tracing or log delivery alone, without Transaction Search, ever produces spans.
- Evaluators other than `Builtin.Helpfulness`, session-level evaluators, and online evaluation configs.
- Anything beyond a single one-turn invocation, and any region but us-east-1.
- A single invocation started about a dozen microVMs and log streams. I did not investigate that.

## Run it

Needs an AWS account with CDK bootstrapped in us-east-1, Node 20+, Python 3.10+ with boto3, and Bedrock access to
Claude Haiku 4.5.

    make deploy      # about a minute: execution role and a Harness
    make baseline    # Transaction Search OFF: expect 0 spans and a failed evaluation
    make demo        # the whole A/B, about 20 to 30 minutes, then reverts the account setting
    make destroy

**`enable` and `revert` change an account-level setting.** Transaction Search applies to the whole account in that Region,
not just this harness, and the spans it stores are billed as CloudWatch Logs. The script records nothing about your other
workloads, so check the setting first (`aws xray get-trace-segment-destination`) and do not run it in a shared account.
`make demo` puts the destination back to `XRay` and removes the one resource policy it added, plus the two log groups
if they are empty.

## Cost

The harness itself has no charge; you pay for what it uses ([docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-operations.html)).
Each invocation sent about 930 input and 10 output tokens to Claude Haiku 4.5 and ran a short microVM session. I did
not price the model tokens, the runtime time, the CloudWatch Logs ingestion for spans, or the evaluator, and I did not
read a measured bill. Treat this as small and unmeasured.
