# EcoCentinela en Google Kubernetes Engine (GKE Autopilot).
#
# Vive en su propio estado de Terraform (prefijo «gke» del mismo bucket) para poder crearlo y
# destruirlo sin tocar el resto de la plataforma, que sigue en Cloud Run:
#   terraform -chdir=infra/gke init -backend-config="bucket=<proyecto>-tfstate" -backend-config="prefix=gke"
#   terraform -chdir=infra/gke apply  -var proyecto=<proyecto>
#   terraform -chdir=infra/gke destroy -var proyecto=<proyecto>
#
# Costo: la tarifa de gestión de un clúster Autopilot (USD 0,10/h) la cubre el crédito mensual
# del nivel gratuito de GKE (USD 74,40 por cuenta de facturación). Se pagan los pods que corren
# (Spot, ~60-90 % más baratos) y la regla de reenvío del balanceador. Por eso se apaga al terminar.

terraform {
  required_version = ">= 1.9"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }
  backend "gcs" {}
}

variable "proyecto" {
  description = "ID del proyecto de Google Cloud"
  type        = string
}

variable "region" {
  description = "Misma región que el resto de la plataforma"
  type        = string
  default     = "us-central1"
}

provider "google" {
  project               = var.proyecto
  region                = var.region
  user_project_override = true
  billing_project       = var.proyecto
}

resource "google_project_service" "gke" {
  for_each           = toset(["container.googleapis.com"])
  service            = each.value
  disable_on_destroy = false
}

# Los nodos NO usan la cuenta de servicio por defecto de Compute Engine (que tiene rol de Editor):
# usan una cuenta propia con el mínimo que un nodo necesita (registros, métricas y bajar imágenes).
resource "google_service_account" "nodos" {
  account_id   = "eco-gke-nodos"
  display_name = "Nodos de GKE (mínimo privilegio)"
}

resource "google_project_iam_member" "nodos" {
  for_each = toset(["roles/container.defaultNodeServiceAccount"])
  project  = var.proyecto
  role     = each.value
  member   = "serviceAccount:${google_service_account.nodos.email}"
}

resource "google_artifact_registry_repository_iam_member" "nodos_bajan_imagenes" {
  location   = var.region
  repository = "ecocentinela"
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.nodos.email}"
}

resource "google_container_cluster" "eco" {
  name                = "ecocentinela"
  location            = var.region # regional: el plano de control está replicado en 3 zonas
  enable_autopilot    = true       # Google administra nodos, parches y escalado; se paga por pod
  deletion_protection = false

  release_channel {
    channel = "REGULAR"
  }

  cluster_autoscaling {
    auto_provisioning_defaults {
      service_account = google_service_account.nodos.email
      oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    }
  }

  depends_on = [google_project_service.gke, google_project_iam_member.nodos]
}

# Workload Identity: cada ServiceAccount de Kubernetes actúa como la cuenta de servicio de Google
# que ya usa ese mismo componente en Cloud Run. Sin claves JSON dentro del clúster.
locals {
  cuentas_k8s = toset(["ingesta", "tablero", "simulador"])
}

resource "google_service_account_iam_member" "workload_identity" {
  for_each           = local.cuentas_k8s
  service_account_id = "projects/${var.proyecto}/serviceAccounts/eco-${each.key}@${var.proyecto}.iam.gserviceaccount.com"
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.proyecto}.svc.id.goog[ecocentinela/${each.key}]"
  depends_on         = [google_container_cluster.eco]
}

output "cluster" {
  value = google_container_cluster.eco.name
}

output "conectar" {
  value = "gcloud container clusters get-credentials ${google_container_cluster.eco.name} --region ${var.region} --project ${var.proyecto}"
}
