"""Shared helpers for the repo's scripts: call the AWS CLI the same way everywhere, fail loudly,
read Terraform outputs, and keep results in build/results/."""
import json
import os
import subprocess
import sys

# The repo folder (where env.sh lives). env.sh exports WORKDIR; fall back to the parent of scripts/.
ROOT = os.environ.get("WORKDIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, "build", "results")


def aws(*args):
    """Run `aws <args> --output json` and return parsed JSON (or {} for empty output)."""
    ok, data, err = aws_try(*args)
    if not ok:
        sys.exit(f"\nFAILED: aws {' '.join(args[:2])}\n{err}\n")
    return data


def aws_try(*args):
    """Like aws(), but returns (ok, data, stderr) instead of exiting."""
    res = subprocess.run(["aws", *args, "--output", "json"], capture_output=True, text=True)
    if res.returncode != 0:
        return False, {}, res.stderr.strip()
    return True, (json.loads(res.stdout) if res.stdout.strip() else {}), ""


def tf_output(name):
    """One Terraform output, parsed (run from anywhere: uses the repo's infra/ folder)."""
    res = subprocess.run(["terraform", f"-chdir={os.path.join(ROOT, 'infra')}", "output", "-json", name],
                         capture_output=True, text=True)
    if res.returncode != 0:
        sys.exit(f"\nFAILED: terraform output {name}\n{res.stderr.strip()}\n")
    return json.loads(res.stdout)


def write_result(name, obj):
    """build/results/<name>.json, pretty-printed (these files go into the repo)."""
    os.makedirs(RESULTS, exist_ok=True)
    path = os.path.join(RESULTS, f"{name}.json")
    with open(path, "w") as f:
        json.dump(obj, f, indent=1, default=str)
    return path


def result_lines(text, prefix="RESULT "):
    """The JSON objects printed on lines that start with prefix."""
    return [json.loads(l[len(prefix):]) for l in text.splitlines() if l.startswith(prefix)]
