# lean-governance-ebs-snapshot-inventory

![License](https://img.shields.io/badge/License-MIT--0-blue.svg)
![Huawei Cloud](https://img.shields.io/badge/Huawei%20Cloud-EVS%20%2F%20Organizations-orange.svg)

## 1. Overview

### Introduction

This practice provides a set of Huawei Cloud native tools to inventory EVS (Elastic Volume Service) snapshots across **all accounts in an organization (Organizations)** with one command and export them as a CSV report. Report fields include: account, snapshot ID, volume size, volume ID, creation time, Owner/Team/instance_id tags, and description.

- Intended users: cloud operations / O&M personnel who need to audit, clean up, or compliance-check cross-account EVS snapshots in a unified way.
- Pain point solved: the native console cannot aggregate a snapshot view across organization accounts; this tool covers all member accounts in a single run.

---

## 2. Table of Contents

- [1. Overview](#1-overview)
- [2. Table of Contents](#2-table-of-contents)
- [3. Architecture Diagram](#3-architecture-diagram)
- [4. Highlights](#4-highlights)
- [5. Cloud Services Used](#5-cloud-services-used)
- [6. Prerequisites](#6-prerequisites)
- [7. Quick Start](#7-quick-start)
- [8. Step-by-Step Deployment](#8-step-by-step-deployment)
- [9. Usage / Verification](#9-usage--verification)
- [10. Cleanup](#10-cleanup)
- [11. Detailed Explanation](#11-detailed-explanation)
- [12. Dependencies](#12-dependencies)
- [13. FAQ / Troubleshooting](#13-faq--troubleshooting)
- [14. Contributing](#14-contributing)
- [15. License](#15-license)
- [16. Contact / Maintainers](#16-contact--maintainers)

---

## 3. Architecture Diagram

```
Management account (execution entity)
│
├─ Organizations ListAccounts ──► enumerate ACTIVE accounts in the organization
│
├─ (member account) IAM Agency CreateTemporaryAccessKeyByAgency
│ └─► obtain temporary AK/SK/Token for the member account
│
├─ EVS ListSnapshots (per account / region) ──► collect snapshot details
│
└─ (optional) TMS ListTagsForResource ─► supplement Owner/Team/instance_id tags
│
▼
CSV report (local CLI file / FunctionGraph return value / optional OBS storage)

Scheduling form (optional): FunctionGraph + TIMER trigger
```

---

## 4. Highlights

- **Zero public NAT**: FunctionGraph calls Huawei Cloud APIs through the default egress; no VPC / subnet / NAT / EIP is created.
- **Executed per invocation**: FunctionGraph runs on demand and the timer triggers on schedule, with no always-on resources.
- **Native cross-account support**: Built on the Huawei Cloud Organizations + Agency delegation model, with no resident resources in member accounts.
- **CLI / function in one**: The same `snapshot_report.py` can run locally or serve as the FunctionGraph entry point.

---

## 5. Cloud Services Used

| Service | Purpose | Notes |
|---|---|---|
| EVS | Snapshot queries (ListSnapshots) | Enumerates member snapshots across accounts |
| Organizations | List accounts in the organization (ListAccounts) | Must run from the management account |
| IAM | Agency + temporary credentials (CreateTemporaryAccessKeyByAgency) | Cross-account temporary credential acquisition |
| FunctionGraph | Scheduling function (snapshot_report) | Zero infrastructure |
| TMS | Snapshot tags (ListTagsForResource) | Optional, supplements Owner/Team/instance_id |
| OBS (optional) | CSV report storage | Enabled as needed |

> **Note**: All services can be created with one command through the Terraform configuration in this repository; to clean up, see [Section 10: Cleanup](#10-cleanup).

---

## 6. Prerequisites

- **Organization-level inventory prerequisite (important)**: **The current account must be the management account of Huawei Cloud Organizations** (or a principal with Organizations read-only plus the corresponding IAM permissions) for `ListAccounts` to return accounts in the organization.
- If the current account is **not the organization management account / Organizations is not enabled**, calling `ListAccounts` returns `Organizations.1015`. This tool **handles this gracefully**: it automatically skips cross-account enumeration, inventories only the **current account's** EVS snapshots with an explicit notice, and still produces a report (see Section 13).
- Locally installed **Terraform >= 1.5**, and Python >= 3.9 (only needed for CLI / packaging).
- Region constraint: the script and function scan a single region (default `cn-north-4`), which must match the region of the snapshots being inventoried.
- **Cross-account prerequisite (one-time)**: Run `src/adapter/create_member_agency.py` in each **member account** to create the agency `OrgSnapshotAgency` (overridable via `var.member_agency_name`) and delegate the management account (see Section 11). Without this step, only the management account's own snapshots can be inventoried.

---

## 7. Quick Start

### Quick Start (One-Click Scheduling Deployment)

```bash
# 1) Configure AK/SK through session environment variables (deploy_code reads via TF_VAR_*)
export HW_ACCESS_KEY="<AK>"
export HW_SECRET_KEY="<SK>"
export HW_REGION_NAME="cn-north-4"
export TF_VAR_hw_access_key="$HW_ACCESS_KEY"
export TF_VAR_hw_secret_key="$HW_SECRET_KEY"

# 2) Initialize (downloads the huaweicloud provider on first run)
cd infra
terraform init

# 3) Preview the resources to be created
terraform plan

# 4) Apply (creates agency + function + timer; no OBS/NAT by default)
# The null_resource automatically uploads the function code after apply with the SDK (self-contained pip build + upload)
terraform apply
```

> Obtain the real AK/SK on the Huawei Cloud "My Credentials" page and provide them through session environment variables / `TF_VAR_*`, which the `null_resource` passes temporarily to the deployment script via `local-exec environment`; do not write credentials into any delivered file (`*.tf`, and `terraform.tfvars` should be gitignored). The code uniformly uses the `<HW_ACCESS_KEY>` placeholder.
>
> If you only change function environment variables (`user_data`), `terraform apply` performs a metadata update; to rebuild the function code, run `terraform apply -replace=huaweicloud_fgs_function.snapshot_report`.

---

## 8. Step-by-Step Deployment

1. `terraform init` → downloads the `huaweicloud/huaweicloud` provider.
2. `terraform plan` → confirm the resource list (should contain only `huaweicloud_identity_agency` / `huaweicloud_fgs_function` / `huaweicloud_fgs_function_trigger`, plus an optional `huaweicloud_obs_bucket`).
3. `terraform apply` → outputs the function URN, agency name, and timer ID.

---

## 9. Usage / Verification

### Usage and Verification

**A. Local CLI (recommended to verify the logic first)**

```bash
export HW_ACCESS_KEY="<AK>"
export HW_SECRET_KEY="<SK>"
# Inventory only the current account
python src/adapter/snapshot_report.py --region cn-north-4 --file out.csv
# Organization-wide inventory (requires agencies created in member accounts)
python src/adapter/snapshot_report.py --region cn-north-4 --file out.csv --agency OrgSnapshotAgency
```

**B. FunctionGraph (scheduling form)**

- After deployment, the function is triggered daily at 08:00 by the TIMER; for manual verification, use "Test" in the console or invoke it synchronously:
```bash
# Invoke synchronously through the FunctionGraph SDK to get the returned CSV
```
- The function return body contains `rows` (number of snapshot records) and `csv` (the full report text).

> After deployment, follow these steps to verify that the business functionality works before considering the delivery complete.

---

## 10. Cleanup

```bash
cd infra
terraform destroy
```

---

## 11. Detailed Explanation

- **Cross-account model**: The management account holds permanent AK/SK; for each member account it calls `CreateTemporaryAccessKeyByAgency(agency_name="OrgSnapshotAgency", session_user.domain_name=<member-account-name>)` to obtain temporary credentials, then builds an EVS client (requiring `KeystoneListAuthProjects` to resolve the member account's `project_id`).
- **Pagination**: EVS `ListSnapshots` pages with `limit=1000, offset` until fewer than a full page is returned.
- **Tags**: First reads inline `tags` from the EVS response; if absent and `--with-tags` is enabled, falls back to TMS (`resource_type="snapshots"`); any failure disables subsequent TMS calls to avoid a flood of errors.
- **Access key configuration**: The CLI reads `HW_ACCESS_KEY/SK`; FunctionGraph reads the agency temporary credentials from the handler's `context`.

### 11a. One-Time Agency Creation in Member Accounts

Execute in **each member account** (with that account's own AK/SK):

```bash
export HW_ACCESS_KEY="<member account AK>"
export HW_SECRET_KEY="<member account SK>"
python src/adapter/create_member_agency.py \
--region cn-north-4 \
--agency-name OrgSnapshotAgency \
--mgmt-domain-id <management account domain id>
```

> The management account domain id can be obtained by calling IAM `KeystoneListAuthDomains` from the management account.
>
> Note the two different agencies:
> - **Execution agency** `ebs-snapshot-fg-agency` (`var.function_agency_name`): the `agency` field of the function resource itself, allowing FunctionGraph to call Huawei Cloud APIs (including `IAM Agency Lambda WhiteList` to call agency temporary keys). Created automatically by Terraform.
> - **Member agency** `OrgSnapshotAgency` (`var.member_agency_name`): created in **each member account** and delegated to the management account; the function uses it with `CreateTemporaryAccessKeyByAgency` to obtain member account temporary credentials. Created by `create_member_agency.py` in each member account.
> The two have different names and scopes; do not confuse them.

---

## 12. Dependencies

- Huawei Cloud Python SDKs: `huaweicloudsdkevs` / `huaweicloudsdkorganizations` / `huaweicloudsdkiam` / `huaweicloudsdktms` / `huaweicloudsdkobs`.
- The cross-account Agency pattern references `Hatz-D/huaweicloud-organizations-accounts-projects`.

---

## 13. FAQ / Troubleshooting

- **`Organizations.1015: this operation can be called from any account in the organization`**: The current account is **not the organization management account** or Organizations is not enabled, so `ListAccounts` is rejected. This tool gracefully falls back to "scan only the current account" with an explicit log message; for organization-wide scanning, run with the organization management account or enable Organizations on that account.
- **Function execution reports 403 (cross-account)**: Confirm that the member account agency has been created and delegates the management account; the function execution agency needs permission to call agency temporary keys (such as `IAM Agency Lambda WhiteList`, and if necessary add the `iam:agencies:createTemporaryAccessKey` custom policy).
- **Function logs are empty / return the default empty response**: The SDK was not packaged at runtime (the function is an empty shell). This practice uses `null_resource` to automatically `pip` build and upload the code after `terraform apply`; if you changed it manually, re-run `terraform apply` or `terraform apply -replace=huaweicloud_fgs_function.snapshot_report`.
- **EVS `limit/offset` returns no data**: Confirm that the region and the member account `project_id` are resolved correctly (the function logs print the account and snapshot count).
- **`ModuleNotFoundError: No module named 'defusedxml'` and similar**: The function package is missing transitive dependencies. Repackage with `src/functions/build_function_package.py` (based on `pip install --target`, including all transitive dependencies).

---

## 14. Contributing

Please keep the two hard constraints in mind for changes: do not persist real credentials to files, and do not create public NAT.

---

## 15. License

This practice is licensed under **MIT No Attribution**; see the [`LICENSE`](LICENSE) file in the repository root for details.

This practice is released under the MIT-0 license.

---

## 16. Contact / Maintainers
| Item | Content |
|---|---|
| **Creator** | [dgc-git](https://github.com/dgc-git) |
| **Maintainer** | [dgc-git](https://github.com/dgc-git) |
| **Contact** | GitHub Issues / PR (see [Section 14](#14-contributing)) |
