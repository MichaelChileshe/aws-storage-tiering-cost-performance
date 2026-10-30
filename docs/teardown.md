# Teardown and clean check

Nothing from this project should exist or bill after the sitting, except the shared state bucket.

## Destroy

`terraform -chdir=infra destroy`: `Plan: 0 to add, 0 to change, 33 to destroy.` → **`Destroy complete! Resources: 33 destroyed.`** Log: [`results/terraform/tf-destroy.txt`](results/terraform/tf-destroy.txt). Screenshot [`26`](screenshots/26-destroy-33.png).

The order Terraform chose, from the log:
- **The archived snapshot was among the first, deleted in 1 s.** Deleting a snapshot in the archive tier is allowed; AWS bills the rest of its 90-day minimum (US$0.0072, computed before it was archived).
- **Both buckets were emptied and deleted** (`force_destroy`): the lab bucket in 3 s, including the objects in Glacier, and the directory bucket in 4 s. Deleting Glacier objects before their 90 days bills the remainder: a fraction of a cent for 8 MiB.
- **The five volume attachments came off in 12–15 s,** detached from the running instance. Then the volumes (11–13 s) and the instance (21 s).
- **Last, the network and IAM:** egress rule, instance profile, role and policy attachment, route, subnet, security group, IGW, route table, and the VPC.

## Clean check

Every query filters on the project tag or the project's names. Screenshot [`27`](screenshots/27-clean-check.png).

| Check | Result |
|---|---|
| `terraform state list` | **0** |
| Instances (pending, running, stopping, stopped) | none |
| Volumes | none |
| Snapshots, including the archive tier | none |
| Snapshots in the Recycle Bin | none |
| General purpose buckets `zenzele-tiering-*` | none |
| Directory buckets `zenzele-express-*` | none (I read the one `None` in the output as this query: it printed `None` the same way before the build, when the account had no directory buckets) |
| Storage Lens configuration `zenzele-tiering-lens` | none |
| IAM roles and instance profiles `zenzele-tiering*` | none |
| VPC, subnet, security group, VPC endpoint, IGW | none |
| CloudWatch alarms `zenzele*` | none (this lab never created any; the check proves it) |
| State object | `terraform.tfstate`, **836 bytes**: an empty state, kept in the shared bucket |

I ran the whole check as one block, so the commands echo first and their output follows together. The screenshot shows the last commands and that whole output: the state count `0`, one `None`, and the 836-byte state file. Every other query printed nothing.

## The next day

Cost Explorer for 30 October 2026, daily, grouped by service, then by usage type for EC2-Other and S3. That gives the actual cost of the sitting, which I'll compare with the list-price estimate in the [cost model](cost-model.md).
