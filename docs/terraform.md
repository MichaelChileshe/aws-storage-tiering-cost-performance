# Terraform

Everything in AWS is in [`infra/`](../infra/): one root module, state in the shared bucket `nkosi-tfstate-<account id>` under the key `aws-storage-tiering-cost-performance/terraform.tfstate`, with S3-native locking (`use_lockfile = true`). AWS provider **6.71.0** (constraint `~> 6.60`), locked in [`infra/.terraform.lock.hcl`](../infra/.terraform.lock.hcl).

| File | What it holds |
|---|---|
| `network.tf` | VPC 10.29.0.0/16, one public subnet pinned to an AZ **ID**, IGW, route table, S3 gateway endpoint, a security group with no inbound rules |
| `iam.tf` | The instance role: `AmazonSSMManagedInstanceCore` plus object access to the lab bucket and `s3express:CreateSession` on the directory bucket |
| `s3.tf` | The lab bucket (Block Public Access, SSE-S3, TLS-only policy), 5 lifecycle rules, the Intelligent-Tiering archive configuration, the Express One Zone directory bucket, the Storage Lens configuration |
| `bench.tf` | The c7g.xlarge, the six volumes and attachments, the export snapshot |
| `variables.tf` | AZ ID (validated against the three Express One Zone AZ IDs in us-east-1), instance type, and the phase variables |

## Phases are variables

| Phase | Line in `infra/phases.auto.tfvars` | Plan | State list |
|---|---|---|---|
| Base | (nothing) | `21 to add` | 23 (21 + 2 data sources: the AZ lookup and the AMI parameter) |
| Bench | `bench_on = true`, `io2_on = true` | `13 to add` (instance, 6 volumes, 6 attachments) | 36 |
| io2 off | remove `io2_on` | `2 to destroy` | 34 |
| Snapshot | `snapshot_on = true` | `1 to add` | |
| gp2 → gp3 | `gp2_to_gp3 = true` | `1 to change`, in place | |
| gp3 tuning | `gp3_iops = 6000`, `gp3_throughput = 500` | `1 to change`, in place | |
| Destroy | | `33 to destroy` | 0 |

Every plan matched what I expected before running it, and every apply and destroy log is in [`results/terraform/`](results/terraform/). Screenshots [`03`](screenshots/03-base-apply-outputs.png), [`04`](screenshots/04-bench-apply-outputs.png), [`08`](screenshots/08-io2-off-plan.png)–[`10`](screenshots/10-state-list-34.png), [`14`](screenshots/14-gp2-to-gp3-plan.png), [`25`](screenshots/25-summary-terraform.png), [`26`](screenshots/26-destroy-33.png).

## Design choices worth knowing

- **One map of volumes.** `aws_ebs_volume.bench` and `aws_volume_attachment.bench` use `for_each` over a map built from the phase variables. Turning `io2_on` off removes one key, so Terraform destroys exactly that attachment and that volume (`because key ["io2"] is not in for_each map`).
- **The gp2 volume keeps its key after it becomes gp3.** The key is the volume's role in the lab, named after the type it starts as. So `gp2_to_gp3` changes `type`, `iops` and `throughput` on the same resource: an in-place update, never a replacement. The provider returned after 13 s on both modifications, without waiting for AWS's `optimizing` phase to end.
- **A precondition guards gp3 throughput:** at most 0.25 MiB/s per provisioned IOPS, so a bad combination fails at plan time instead of at the AWS API.
- **AZ ID, not name.** The subnet, volumes and the directory bucket all use `use1-az4`; the volumes get the account's AZ name through `data.aws_availability_zone`. The directory bucket's name has to contain the AZ ID anyway (`--use1-az4--x-s3`).
- **The agent travels as gzipped user data.** `bench_agent.py` is 15.9 KB; base64-encoded it would be about 21 KB, over EC2's 16 KB user-data limit. Terraform sends it through `base64gzip()` (about 7 KB) and the boot script unpacks it. The `agent_sha256` output (`9de5007a…`) matched the hash the instance reported, and it matches the file in this repo.
- **Bucket names carry a random suffix** made once on my machine (`infra/name.auto.tfvars`, not committed): bucket names are global, and nothing in them points back to the account ID.
- **`force_destroy` on both buckets,** because the test objects (including the Glacier ones) are written by the instance, not by Terraform. The destroy empties both buckets first.
- **The snapshot's tier is not Terraform's business.** `snapshot_archive.py` moves it to the archive tier, and `ignore_changes = [storage_tier]` stops Terraform from trying to move it back.

## Quality gates (before any apply)

`terraform fmt -check`, `terraform validate` and `tflint` were clean. checkov: **58 passed, 14 failed** ([`results/terraform/checkov.txt`](results/terraform/checkov.txt), screenshot [`02`](screenshots/02-init-and-gates.png)). The scan covered `infra/` and `bootstrap/` (the state bucket).

| Finding | Where | Why it's like that here | What I'd do in production |
|---|---|---|---|
| CKV_AWS_145 KMS by default | lab bucket, state bucket | SSE-S3 is enough for test objects | SSE-KMS with a customer managed key for patient data |
| CKV_AWS_189 EBS with a customer managed key | bench volumes | Encrypted with the AWS managed key | Customer managed key, with key policy and rotation |
| CKV_AWS_18 access logging | both buckets | One-day lab | Server access logs or CloudTrail data events |
| CKV_AWS_21 versioning | lab bucket | Objects are disposable | Versioning (and Object Lock) on records |
| CKV_AWS_144 cross-region replication | both buckets | Out of scope | Replication for the PACS archive, if the DR plan needs it |
| CKV2_AWS_62 event notifications | both buckets | Nothing consumes them | Only where something should react |
| CKV_AWS_300 abort failed multipart uploads | state bucket | The lab bucket has the rule; the state bucket (separate project) doesn't | Add the same 7-day rule |
| CKV_AWS_130 public IP by default | subnet | The instance needs a route to SSM and the package repositories, without NAT | Private subnet with VPC endpoints for SSM |
| CKV2_AWS_11 VPC flow logs | VPC | One-day lab | Flow logs to S3 |
| CKV2_AWS_12 default security group | VPC | Unused, but not emptied | Manage the default security group with no rules |
