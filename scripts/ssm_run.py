#!/usr/bin/env python3
"""Run a shell command on the bench instance through SSM Run Command, wait, print the output.
No SSH, no key pair, no inbound port: the instance's SSM agent pulls the command.

  python3 scripts/ssm_run.py <region> "<shell command>" <instance-id> [<instance-id> ...]

Other scripts import run() (send + wait) or send() and wait() separately, when they need to do
something else (poll CloudWatch) while the command runs.
"""
import json
import subprocess
import sys
import time

from kit_aws import aws


def wait_online(iids, region, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        info = aws("ssm", "describe-instance-information", "--region", region,
                   "--filters", f"Key=InstanceIds,Values={','.join(iids)}")["InstanceInformationList"]
        online = {i["InstanceId"] for i in info if i["PingStatus"] == "Online"}
        if online >= set(iids):
            return
        print(f"  waiting for {len(set(iids) - online)} instance(s) in {region} to register with Systems Manager ...",
              flush=True)
        time.sleep(15)
    sys.exit(f"instances in {region} did not come Online in SSM within {timeout // 60} minutes")


def _quiet(*args):
    r = subprocess.run(["aws", *args, "--output", "json"], capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None


def send(iids, command, region, exec_timeout=600):
    """Start the command; return its CommandId. exec_timeout = seconds SSM lets it run on the instance."""
    iids = list(iids)
    wait_online(iids, region)
    return aws("ssm", "send-command", "--region", region, "--instance-ids", *iids,
               "--document-name", "AWS-RunShellScript", "--comment", command[:100],
               "--timeout-seconds", "600",
               "--parameters", json.dumps({"commands": [command], "executionTimeout": [str(exec_timeout)]})
               )["Command"]["CommandId"]


def poll(cid, iid, region):
    """One look at the command on one instance: (status, stdout, stderr); status None = not visible yet."""
    res = _quiet("ssm", "get-command-invocation", "--region", region, "--command-id", cid, "--instance-id", iid)
    if not res:
        return None, "", ""
    status = res["Status"] + (f" / {res.get('StatusDetails')}" if res.get("StatusDetails") else "")
    return status, res.get("StandardOutputContent", "").rstrip(), res.get("StandardErrorContent", "").rstrip()


def wait(cid, iids, region, echo=True, timeout=180, started=None):
    """Wait for a sent command on every instance; return {instance_id: stdout}. Exits if any fails."""
    iids = list(iids)
    t0 = started or time.time()
    out, failed = {}, []
    pending, last, last_print = set(iids), {}, time.time()
    while pending:
        time.sleep(2)
        if time.time() - t0 > timeout:
            for iid in sorted(pending):
                print(f"[{region} {iid}] no result after {timeout} s, last status: {last.get(iid, 'no invocation yet')}")
            sys.exit(f"command {cid} did not finish on {', '.join(sorted(pending))}")
        if time.time() - last_print > 15:
            print(f"  still running ({time.time() - t0:.0f} s): " +
                  ", ".join(f"{i} ({last.get(i, 'no invocation yet')})" for i in sorted(pending)), flush=True)
            last_print = time.time()
        for iid in sorted(pending):
            status, stdout, stderr = poll(cid, iid, region)
            if status:
                last[iid] = status
            if not status or status.split(" ")[0] in ("Pending", "InProgress", "Delayed"):
                continue
            pending.discard(iid)
            out[iid] = stdout
            if echo or not status.startswith("Success"):  # a failure always shows what the instance said
                print(f"[{region} {iid}] {status} in {time.time() - t0:.0f} s")
                if stdout:
                    print(stdout)
                if stderr:
                    print("----- stderr -----\n" + stderr)
            if not status.startswith("Success"):
                failed.append(iid)
    if failed:
        sys.exit(f"command failed on {', '.join(failed)}")
    return out


def run(iids, command, region, echo=True, timeout=180):
    """Run one shell command on every instance at once and wait; return {instance_id: stdout}."""
    cid = send(iids, command, region, exec_timeout=max(300, timeout + 60))
    # the clock starts once the command is sent: waiting for SSM registration doesn't count
    return wait(cid, iids, region, echo=echo, timeout=timeout, started=time.time())


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    run(sys.argv[3:], sys.argv[2], sys.argv[1])


if __name__ == "__main__":
    main()
