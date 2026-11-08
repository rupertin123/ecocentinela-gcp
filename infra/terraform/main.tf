# EcoCentinela en Google Cloud: toda la infraestructura declarada como código.
# Regla de diseño: cada recurso tiene que entrar en el nivel gratuito de Google Cloud.

terraform {
  required_version = ">= 1.9"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 7.0"
    }
  }
  # El estado vive en un bucket de us-central1 (Cloud Storage es gratis sólo en us-east1/us-west1/us-central1).
  backend "gcs" {}
}

provider "google" {
  project               = var.proyecto
  region                = var.region
  user_project_override = true
  billing_project       = var.proyecto
}

provider "google-beta" {
  project               = var.proyecto
  region                = var.region
  user_project_override = true
  billing_project       = var.proyecto
}

data "google_project" "p" {}

# Los «agentes de servicio» de Google se crean recién cuando se usan por primera vez;
# acá se fuerzan para poder darles permisos desde el inicio.
resource "google_project_service_identity" "agentes" {
  provider   = google-beta
  for_each   = toset(["pubsub.googleapis.com", "bigquerydatatransfer.googleapis.com"])
  service    = each.value
  depends_on = [google_project_service.apis]
}

locals {
  repo_imagenes = "${var.region}-docker.pkg.dev/${var.proyecto}/ecocentinela"
  apis = [
    "run.googleapis.com", "pubsub.googleapis.com", "bigquery.googleapis.com",
    "bigquerydatatransfer.googleapis.com", "firestore.googleapis.com", "cloudscheduler.googleapis.com",
    "artifactregistry.googleapis.com", "cloudbuild.googleapis.com", "secretmanager.googleapis.com",
    "monitoring.googleapis.com", "logging.googleapis.com", "iam.googleapis.com",
    "iamcredentials.googleapis.com", "sts.googleapis.com", "billingbudgets.googleapis.com",
    "cloudbilling.googleapis.com", "cloudresourcemanager.googleapis.com", "aiplatform.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}

# ---------------------------------------------------------------- Artifact Registry
resource "google_artifact_registry_repository" "imagenes" {
  repository_id = "ecocentinela"
  location      = var.region
  format        = "DOCKER"
  description   = "Imágenes de los servicios de EcoCentinela"

  # El nivel gratuito es de 0,5 GB: se guardan sólo las 2 versiones más nuevas de cada imagen.
  cleanup_policy_dry_run = false
  cleanup_policies {
    id     = "conservar-2-recientes"
    action = "KEEP"
    most_recent_versions {
      keep_count = 2
    }
  }
  cleanup_policies {
    id     = "borrar-viejas"
    action = "DELETE"
    condition {
      older_than = "86400s"
    }
  }
  depends_on = [google_project_service.apis]
}

# Bucket regional para el código fuente que sube Cloud Build (el bucket por defecto es multirregional y no es gratis).
resource "google_storage_bucket" "construccion" {
  name                        = "${var.proyecto}-construccion"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true
  lifecycle_rule {
    condition {
      age = 7
    }
    action {
      type = "Delete"
    }
  }
}
