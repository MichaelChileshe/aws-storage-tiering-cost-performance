"""Lab helpers on top of kit_aws: Terraform outputs, the bench instance, its volumes, and the bench
agent (app/bench_agent.py on the instance) called through SSM."""
import json
import os
import sys
from datetime import datetime, timezone

from kit_aws import RESULTS, aws, result_lines, tf_output
from ssm_run import run, send, wait

AGENT = "python3 /opt/bench/bench_agent.py"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def region():
    return tf_output("region")


def instance_id():
    iid = tf_output("bench_instance_id")
    if not iid:
        sys.exit("no bench instance: set bench_on = true in infra/phases.auto.tfvars and apply first")
    return iid


def volumes():
    """{role: {id, type, size, iops, throughput}} as Terraform last applied it."""
    v = tf_output("volumes")
    if not v:
        sys.exit("no bench volumes in the state: bench_on = true first")
    return v


def volume_now(vol_id, reg):
    """The volume as EC2 reports it right now (type/IOPS/throughput change during a modification)."""
    v = aws("ec2", "describe-volumes", "--region", reg, "--volume-ids", vol_id)["Volumes"][0]
    return {"id": vol_id, "type": v["VolumeType"], "size": v["Size"], "iops": v.get("Iops"),
            "throughput": v.get("Throughput"), "state": v["State"]}


def agent(args, timeout=180, echo=False):
    """Run the bench agent with these arguments on the instance; return its RESULT objects."""
    iid = instance_id()
    out = run([iid], f"{AGENT} {args}", region(), echo=echo, timeout=timeout)[iid]
    res = result_lines(out)
    if not res:
        print(out)
        sys.exit("the agent printed no RESULT line")
    return res


def agent_send(args, exec_timeout=600):
    """Start the agent without waiting; returns (command_id, instance_id)."""
    iid = instance_id()
    return send([iid], f"{AGENT} {args}", region(), exec_timeout=exec_timeout), iid


def agent_wait(cid, iid, timeout, started):
    out = wait(cid, [iid], region(), echo=False, timeout=timeout, started=started)[iid]
    return result_lines(out)


def append_jsonl(name, rows):
    os.makedirs(RESULTS, exist_ok=True)
    with open(os.path.join(RESULTS, f"{name}.jsonl"), "a") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")


def read_jsonl(name):
    p = os.path.join(RESULTS, f"{name}.jsonl")
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in open(p) if l.strip()]
