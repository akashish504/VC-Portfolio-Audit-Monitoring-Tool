variable "aws_region" {
  description = "AWS region for EKS and ACM"
  type        = string
}

variable "aws_account_id" {
  description = "Expected AWS account ID guardrail"
  type        = string
}

variable "eks_cluster_name" {
  description = "Target EKS cluster name"
  type        = string
}

variable "namespace" {
  description = "Kubernetes namespace"
  type        = string
}

variable "app_name" {
  description = "Kubernetes app label and base resource name"
  type        = string
}

variable "image" {
  description = "Container image, including tag"
  type        = string
}

variable "replicas" {
  description = "Number of replicas"
  type        = number
  default     = 1
}

variable "container_port" {
  description = "App container port"
  type        = number
  default     = 80
}

variable "service_port" {
  description = "Service port"
  type        = number
  default     = 80
}

variable "host" {
  description = "Ingress host"
  type        = string
}

variable "acm_certificate_arn" {
  description = "ACM certificate ARN for ALB ingress"
  type        = string
}

variable "inbound_cidrs" {
  description = "CIDRs allowed to reach the ALB. Defaults to open; set to a restricted list to lock down the public UI."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "doppler_secret_name" {
  description = "K8s secret name containing DOPPLER_TOKEN"
  type        = string
}
