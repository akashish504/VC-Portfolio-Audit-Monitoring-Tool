# Fill these with real infra values before first init.
bucket         = "REPLACE_WITH_TERRAFORM_STATE_BUCKET"
key            = "portfolio-review-audit-frontend/prod/k8s-app.tfstate"
region         = "ap-southeast-1"
dynamodb_table = "REPLACE_WITH_TERRAFORM_LOCK_TABLE"
encrypt        = true
