# Terraform: Prod K8s App Deployment

This stack is intentionally **prod-only** and manages:
- Deployment
- Service
- Ingress (ALB + ACM)

It is designed to avoid any impact to dev.

## Guardrails
- Expected account: `105849548382`
- Expected cluster: `peakxv-master-prod-apps`
- Expected namespace: `portfolio-review`

If these do not match, plan/apply fails.

## Bootstrap
1. Populate `backend-prod.hcl` with real S3/DynamoDB values.
2. Update `environments/prod.tfvars` image tag.
3. Run:

```bash
cd infra/terraform/k8s-app
terraform init -backend-config=backend-prod.hcl
terraform plan -var-file=environments/prod.tfvars
```

## Import existing resources (first-time adoption)

```bash
terraform import -var-file=environments/prod.tfvars 'kubernetes_deployment_v1.app' 'portfolio-review/portfolio-review-app-ui-deployment'
terraform import -var-file=environments/prod.tfvars 'kubernetes_service_v1.app' 'portfolio-review/portfolio-review-app-ui-service'
terraform import -var-file=environments/prod.tfvars 'kubernetes_ingress_v1.app' 'portfolio-review/portfolio-review-app-ui-ingress'
```

Then run `terraform plan` until drift is understood and acceptable.
