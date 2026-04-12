# Grafana — Waste Reconciliation Dashboard

Self-hosted Grafana on EC2 that visualises data reconciliation as a BI dashboard. Fully automated via CloudFormation — no manual SSH or file-transfer steps after deploy.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| AWS CLI configured | `aws sts get-caller-identity` should return your account |
| IAM permissions | CloudFormation, EC2, IAM (for the instance role) |
| EC2 key pair | Must already exist in **us-east-1**. Used for SSH access only. |
| VPC | Deploys into `fastapi-app-vpc` (`vpc-0237dd5f0dc025eaa`) by default |

---

## Deploy (first time)

Run from the **repo root**:

```bash
bash deployment/aws/grafana/deploy.sh
```

The script will prompt for:
- **EC2 key pair name** — the name of your existing key pair (e.g. `grafana-ec2-key`)
- **Your CIDR** — restrict SSH and Grafana UI access to your IP (e.g. `1.2.3.4/32`), or `0.0.0.0/0` to open to all

CloudFormation will then:
1. Create an IAM role so the instance can signal back to CloudFormation
2. Create a security group in `fastapi-app-vpc`
3. Launch an Ubuntu 22.04 `t3.small` EC2 instance
4. **UserData** installs Grafana + the Infinity plugin
5. **cfn-init** places provisioning files (datasource + dashboard) before Grafana starts
6. Grafana starts with the `datarecon-infinity` datasource and Waste Reconciliation dashboard already loaded
7. `cfn-signal` reports success — the CLI returns once everything is confirmed running

Stack creation takes **8–12 minutes** (most of the time is `apt-get upgrade`).

---

## Update (stack already exists)

Re-run the same script — it detects the existing stack and updates it:

```bash
bash deployment/aws/grafana/deploy.sh
```

> If you change the dashboard JSON or provisioning files in `cloudformation.yaml`, a stack update will replace the EC2 instance with a freshly bootstrapped one.

---

## Access Grafana

After the deploy script exits, the Grafana URL is printed in the output table. It will also be:

```
http://<PublicIP>:3000
```

- **Login**: `admin` / `admin` — you will be prompted to change the password on first login
- **Dashboard**: Dashboards → datarecon → **Waste Reconciliation**

---

## Architecture

```
cloudformation.yaml
├── GrafanaEC2Role          IAM role — allows cfn-init to read stack metadata
├── GrafanaInstanceProfile  Attaches the role to the EC2 instance
├── GrafanaSecurityGroup    Ports 22 (SSH) and 3000 (Grafana UI)
└── GrafanaEC2Instance
    ├── Metadata (cfn-init)
    │   ├── /etc/grafana/provisioning/datasources/infinity.yaml
    │   │     Infinity plugin datasource, uid: datarecon-infinity
    │   │     base_url: datarecon API Gateway (us-east-1)
    │   ├── /etc/grafana/provisioning/dashboards/dashboard.yaml
    │   │     File-based dashboard provider pointing to /var/lib/grafana/dashboards/
    │   └── /var/lib/grafana/dashboards/datarecon.json
    │         Waste Reconciliation dashboard — calls /v1/reconcile and /v1/health
    └── UserData
          1. apt-get: grafana, python3-pip
          2. grafana-cli: install yesoreyeram-infinity-datasource
          3. grafana.ini: allow_loading_unsigned_plugins
          4. cfn-init: place provisioning files, enable + start grafana-server
          5. cfn-signal: notify CloudFormation of success/failure
```

---

## API calls made by the dashboard

| Panel | Endpoint | Notes |
|---|---|---|
| Service Health | `GET /v1/health` | Returns `{"status": "ok"}` |
| All other panels | `GET /v1/reconcile?year=<Y>&month=<M>` | Year/month set by dashboard dropdowns |

The datasource UID is hardcoded as `datarecon-infinity` in both the provisioning YAML and the dashboard JSON — no auto-generated UIDs that can drift between deploys.

---

## Tear down

```bash
aws cloudformation delete-stack --stack-name grafana-datarecon
aws cloudformation wait stack-delete-complete --stack-name grafana-datarecon
```

---

## Files

```
deployment/aws/grafana/
├── cloudformation.yaml          Main stack — self-contained, no S3 dependency
├── deploy.sh                    Create/update helper script
├── README.md                    This file
└── provisioning/                Source of truth for dashboard/datasource config
    ├── datasources/
    │   └── infinity.yaml        Embedded in cloudformation.yaml via cfn-init
    └── dashboards/
        ├── dashboard.yaml       Embedded in cloudformation.yaml via cfn-init
        └── datarecon.json       Embedded in cloudformation.yaml via cfn-init
```

> The `provisioning/` files are the **source of truth**. After editing them, copy the changes into the matching `cfn-init` `files:` blocks in `cloudformation.yaml` and redeploy.
