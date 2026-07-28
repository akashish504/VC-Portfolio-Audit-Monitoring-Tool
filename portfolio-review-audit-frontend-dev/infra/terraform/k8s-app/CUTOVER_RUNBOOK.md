# Terraform Cutover Runbook (Prod Only)

This runbook migrates prod K8s resources from kubectl-manifest ownership to Terraform with no dev impact.

## Preconditions
- Branch contains Terraform files under `infra/terraform/k8s-app`.
- `backend-prod.hcl` has real S3 backend + DynamoDB lock values.
- GitHub environments `production` has required reviewers enabled.
- GitHub Actions secrets are set:
  - `TERRAFORM_PROD_PLAN_ROLE_ARN`
  - `TERRAFORM_PROD_APPLY_ROLE_ARN`
- Existing prod resources are healthy.

## Safety Rules
- Do not change `deployment/k8s/dev/*`.
- Do not run Terraform against any cluster except `peakxv-master-prod-apps`.
- Do not run Terraform with any account except `105849548382`.

## Step 1: Initialize Terraform Locally
```bash
cd infra/terraform/k8s-app
terraform init -backend-config=backend-prod.hcl
terraform validate
```

## Step 2: Import Existing Prod Resources
```bash
terraform import -var-file=environments/prod.tfvars 'kubernetes_deployment_v1.app' 'portfolio-review/portfolio-review-app-ui-deployment'
terraform import -var-file=environments/prod.tfvars 'kubernetes_service_v1.app' 'portfolio-review/portfolio-review-app-ui-service'
terraform import -var-file=environments/prod.tfvars 'kubernetes_ingress_v1.app' 'portfolio-review/portfolio-review-app-ui-ingress'
```

## Step 3: Resolve Drift
```bash
terraform plan -var-file=environments/prod.tfvars
```
- Goal: no destructive changes.
- If there is drift, update Terraform code to match live behavior before first apply.

## Step 4: Enable Plan in PRs
- Open PR with Terraform changes.
- Confirm `Terraform Prod Plan` workflow passes.

## Step 5: First Apply via Manual Workflow
- Trigger `Terraform Prod Apply` workflow manually.
- Provide `image_tag` as release SHA.
- Ensure required reviewers approve the `production` environment gate.

## Step 6: Post-Apply Validation
- Confirm deployment rollout and pods healthy.
- Confirm ingress resolves `pr.peakxv.app` and TLS is valid.

## Step 7: Disable Manifest-Based Prod Apply (After 1-2 Stable Cycles)
- In the existing deployment workflow, stop applying `deployment/k8s/prod/*`.
- Keep `deployment/k8s/dev/*` behavior unchanged.

## Rollback
- Re-run `Terraform Prod Apply` with last known good `image_tag`.
- If needed, temporarily revert to kubectl apply for prod manifests until Terraform drift is fixed.
