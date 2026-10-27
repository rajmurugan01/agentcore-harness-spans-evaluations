#!/usr/bin/env python3
"""A/B test: does an AgentCore Harness deliver spans, and can Evaluations score it, with CloudWatch Transaction Search OFF vs ON?

Commands:
  baseline   invoke the harness, then report spans delivered and run a batch evaluation (run with Transaction Search OFF)
  enable     turn Transaction Search on (ACCOUNT-WIDE for this Region; reversible with `revert`)
  after      same as baseline (run with Transaction Search ON)
  revert     put the trace segment destination back to X-Ray and remove the resource policy this script added
  all        revert-to-baseline-state, baseline, enable, after, then revert (pass --keep to leave Transaction Search on)

`enable` and `revert` change account-level settings. Read the README before running them.
"""
import collections, json, os, sys, time, uuid
import boto3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = json.load(open(os.path.join(ROOT, "cdk", "cdk.outputs.json")))["HarnessSpansEvaluations"]
REGION = OUT["Region"]
POLICY_NAME = "HarnessSpansDemoTransactionSearch"
ctl = boto3.client("bedrock-agentcore-control", region_name=REGION)
rt = boto3.client("bedrock-agentcore", region_name=REGION)
logs = boto3.client("logs", region_name=REGION)
xray = boto3.client("xray", region_name=REGION)
sts = boto3.client("sts", region_name=REGION)


def say(msg=""):
    print(msg, flush=True)


def harness_info():
    h = ctl.get_harness(harnessId=OUT["HarnessId"]); h = h.get("harness", h)
    env = h["environment"]["agentCoreRuntimeEnvironment"]
    return env["agentRuntimeId"], env["agentRuntimeName"]


def runtime_group():
    rid, _ = harness_info()
    return f"/aws/bedrock-agentcore/runtimes/{rid}-DEFAULT"


def destination():
    d = xray.get_trace_segment_destination(); return d["Destination"], d["Status"]


def group_exists(name):
    return any(g["logGroupName"] == name for g in logs.describe_log_groups(logGroupNamePrefix=name)["logGroups"])


def wait_destination(target, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d, s = destination()
        if d == target and s == "ACTIVE":
            say(f"  trace segment destination: {d} {s} (after {int(time.time()-t0)}s)"); return True
        time.sleep(15)
    say(f"  TIMEOUT waiting for {target} ACTIVE; now {destination()}"); return False


def read_stream(group, stream):
    out, tok = [], None
    while True:
        kw = dict(logGroupName=group, logStreamName=stream, startFromHead=True, limit=10000)
        if tok: kw["nextToken"] = tok
        try: r = logs.get_log_events(**kw)
        except logs.exceptions.ResourceNotFoundException: return out
        out += r["events"]
        if r["nextForwardToken"] == tok or not r["events"]: break
        tok = r["nextForwardToken"]
    return out


def invoke(prefix):
    sid = f"{prefix}-{uuid.uuid4().hex}-session"
    r = rt.invoke_harness(harnessArn=OUT["HarnessRef"], runtimeSessionId=sid,
                          messages=[{"role": "user", "content": [{"text": "What is 2+2?"}]}])
    reply = "".join(ev.get("contentBlockDelta", {}).get("delta", {}).get("text", "") for ev in r["stream"])
    say(f"  invoked session {sid}\n  reply: {reply.strip()}")
    return sid


def spans_for(sid, wait):
    """Poll the runtime log group's `spans` stream for this session. Returns span names found."""
    g = runtime_group(); t0 = time.time(); mine = []
    while True:
        ev = [e for e in read_stream(g, "spans") if sid in e["message"]]
        if ev or time.time() - t0 >= wait: mine = ev; break
        time.sleep(15)
    names = collections.Counter()
    for e in mine:
        try: names[json.loads(e["message"]).get("name")] += 1
        except Exception: pass
    return dict(names), int(time.time() - t0)


def evaluate(label, sid):
    _, rname = harness_info()
    groups = [runtime_group()]
    name = f"{label}_{int(time.time())}"[:48]
    r = rt.start_batch_evaluation(batchEvaluationName=name, evaluators=[{"evaluatorId": "Builtin.Helpfulness"}],
        dataSourceConfig={"cloudWatchLogs": {"serviceNames": [f"{rname}.DEFAULT"], "logGroupNames": groups,
                                             "filterConfig": {"sessionIds": [sid]}}})
    bid = r["batchEvaluationId"]; s = {}
    for _ in range(80):
        s = rt.get_batch_evaluation(batchEvaluationId=bid)
        if s.get("status") not in ("PENDING", "IN_PROGRESS", "RUNNING", "STARTING", "CREATING"): break
        time.sleep(8)
    res = s.get("evaluationResults", {})
    say(f"  batch evaluation: {s.get('status')}  sessions completed={res.get('numberOfSessionsCompleted')} failed={res.get('numberOfSessionsFailed')}")
    for es in res.get("evaluatorSummaries", []):
        say(f"    {es['evaluatorId']}: average score {es.get('statistics', {}).get('averageScore')}")
    cw = s.get("outputConfig", {}).get("cloudWatchConfig", {})
    try:
        for e in logs.get_log_events(logGroupName=cw["logGroupName"], logStreamName=cw["logStreamName"], startFromHead=True, limit=10)["events"]:
            a = json.loads(e["message"]).get("attributes", {})
            if a.get("error.message"): say(f"    error.type={a.get('error.type')}\n    error.message={a['error.message']}")
    except Exception:
        pass
    return s.get("status")


def phase(title, prefix, span_wait):
    say(f"\n== {title} ==")
    d, st = destination(); say(f"  trace segment destination at start: {d} {st}")
    say(f"  log group aws/spans exists: {group_exists('aws/spans')}")
    sid = invoke(prefix)
    names, waited = spans_for(sid, span_wait)
    say(f"  spans delivered for this session (runtime log group, 'spans' stream): {sum(names.values())} after {waited}s")
    if names: say(f"  span names: {names}")
    return evaluate(prefix, sid)


def enable():
    acct = sts.get_caller_identity()["Account"]
    say("\n== enabling Transaction Search (account-wide for this Region) ==")
    say(f"  prior destination: {destination()}")
    doc = open(os.path.join(ROOT, "docs", "transaction-search-policy.json")).read().replace("${ACCOUNT_ID}", acct)
    logs.put_resource_policy(policyName=POLICY_NAME, policyDocument=doc)
    time.sleep(12)  # let the resource policy propagate before the destination update
    xray.update_trace_segment_destination(Destination="CloudWatchLogs")
    wait_destination("CloudWatchLogs")


def revert():
    say("\n== reverting Transaction Search ==")
    try: xray.update_trace_segment_destination(Destination="XRay")
    except Exception as e: say(f"  update to XRay: {str(e)[:160]}")
    # Switching back took longer than switching on in my runs (one revert sat PENDING well past 5 minutes), so wait up to 25 minutes
    # and only remove the resource policy once the destination is ACTIVE again.
    if not wait_destination("XRay", 1500):
        say("  destination did not settle; leaving the resource policy and log groups in place. Re-run `revert` later."); return
    try: logs.delete_resource_policy(policyName=POLICY_NAME); say(f"  deleted resource policy {POLICY_NAME}")
    except logs.exceptions.ResourceNotFoundException: pass
    for g in ("aws/spans", "/aws/application-signals/data"):
        try:
            info = [x for x in logs.describe_log_groups(logGroupNamePrefix=g)["logGroups"] if x["logGroupName"] == g]
            if info and not info[0].get("storedBytes") and not logs.filter_log_events(logGroupName=g, limit=1)["events"]:
                logs.delete_log_group(logGroupName=g); say(f"  deleted empty log group {g}")
        except Exception as e: say(f"  left {g} in place: {str(e)[:80]}")
    say(f"  final destination: {destination()}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd == "baseline": phase("BASELINE (Transaction Search OFF)", "baseline", 150)
    elif cmd == "enable": enable()
    elif cmd == "after": phase("AFTER (Transaction Search ON)", "after", 420)
    elif cmd == "revert": revert()
    elif cmd == "all":
        if destination()[0] != "XRay": revert()
        phase("BASELINE (Transaction Search OFF)", "baseline", 150)
        enable()
        phase("AFTER (Transaction Search ON)", "after", 420)
        if "--keep" not in sys.argv: revert()
    else: print(__doc__); sys.exit(2)


if __name__ == "__main__":
    main()
