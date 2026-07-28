locals {
  deployment_name = "${var.app_name}-deployment"
  service_name    = "${var.app_name}-service"
  ingress_name    = "${var.app_name}-ingress"
}

resource "kubernetes_deployment_v1" "app" {
  metadata {
    name      = local.deployment_name
    namespace = var.namespace
    labels = {
      app = var.app_name
    }
  }

  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.current.account_id == var.aws_account_id
      error_message = "Guardrail failed: wrong AWS account."
    }
    precondition {
      condition     = var.eks_cluster_name == "peakxv-master-prod-apps"
      error_message = "Guardrail failed: this stack is prod-only and must target peakxv-master-prod-apps."
    }
    precondition {
      condition     = var.namespace == "portfolio-review"
      error_message = "Guardrail failed: this stack is prod-only and must target namespace portfolio-review."
    }
  }

  spec {
    replicas = var.replicas

    selector {
      match_labels = {
        app = var.app_name
      }
    }

    template {
      metadata {
        labels = {
          app = var.app_name
        }
      }

      spec {
        container {
          name              = var.app_name
          image             = var.image
          image_pull_policy = "Always"

          security_context {
            allow_privilege_escalation = false
          }

          resources {
            limits = {
              cpu    = "500m"
              memory = "800Mi"
            }
            requests = {
              cpu    = "250m"
              memory = "400Mi"
            }
          }

          port {
            name           = "http"
            container_port = var.container_port
          }

          env {
            name = "DOPPLER_TOKEN"
            value_from {
              secret_key_ref {
                name = var.doppler_secret_name
                key  = "DOPPLER_TOKEN"
              }
            }
          }

          liveness_probe {
            http_get {
              path = "/"
              port = var.container_port
            }
            initial_delay_seconds = 10
            period_seconds        = 10
          }

          readiness_probe {
            http_get {
              path = "/"
              port = var.container_port
            }
            initial_delay_seconds = 5
            period_seconds        = 5
          }
        }
      }
    }
  }
}

resource "kubernetes_service_v1" "app" {
  metadata {
    name      = local.service_name
    namespace = var.namespace
  }

  spec {
    type = "ClusterIP"

    selector = {
      app = var.app_name
    }

    port {
      name        = "http"
      protocol    = "TCP"
      port        = var.service_port
      target_port = var.container_port
    }
  }
}

resource "kubernetes_ingress_v1" "app" {
  metadata {
    name      = local.ingress_name
    namespace = var.namespace
    annotations = {
      "kubernetes.io/ingress.class"                            = "alb"
      "alb.ingress.kubernetes.io/scheme"                       = "internet-facing"
      "alb.ingress.kubernetes.io/inbound-cidrs"                = join(",", var.inbound_cidrs)
      "alb.ingress.kubernetes.io/target-type"                  = "ip"
      "alb.ingress.kubernetes.io/listen-ports"                 = "[{\"HTTP\": 80}, {\"HTTPS\": 443}]"
      "alb.ingress.kubernetes.io/ssl-redirect"                 = "443"
      "alb.ingress.kubernetes.io/certificate-arn"              = var.acm_certificate_arn
      "alb.ingress.kubernetes.io/healthcheck-path"             = "/"
      "alb.ingress.kubernetes.io/healthcheck-interval-seconds" = "15"
      "alb.ingress.kubernetes.io/healthcheck-timeout-seconds"  = "5"
      "alb.ingress.kubernetes.io/success-codes"                = "200"
    }
  }

  spec {
    rule {
      host = var.host
      http {
        path {
          path      = "/"
          path_type = "Prefix"
          backend {
            service {
              name = kubernetes_service_v1.app.metadata[0].name
              port {
                number = var.service_port
              }
            }
          }
        }
      }
    }
  }
}
