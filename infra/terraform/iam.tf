# Una cuenta de servicio por componente, cada una con el permiso mínimo que necesita.

locals {
  cuentas = {
    ingesta      = "API de ingesta (Cloud Run)"
    tablero      = "Tablero web (Cloud Run)"
    simulador    = "Flota simulada (Cloud Run Job)"
    programa     = "Cloud Scheduler: dispara el simulador"
    bqml         = "Consultas programadas de BigQuery (modelo y calidad)"
    corte        = "Corte automático de facturación"
    pubsub-push  = "Pub/Sub: entrega las alertas de presupuesto al corte"
    construccion = "Cloud Build: construye las imágenes"
    despliegue   = "GitHub Actions (vía Workload Identity Federation)"
  }
}

resource "google_service_account" "sa" {
  for_each     = local.cuentas
  account_id   = "eco-${each.key}"
  display_name = each.value
  depends_on   = [google_project_service.apis]
}

# --- permisos a nivel proyecto (sólo los que no se pueden acotar a un recurso)
locals {
  roles_proyecto = {
    "ingesta-firestore"   = ["ingesta", "roles/datastore.user"]
    "tablero-firestore"   = ["tablero", "roles/datastore.user"]
    "tablero-bqjobs"      = ["tablero", "roles/bigquery.jobUser"]
    "simulador-firestore" = ["simulador", "roles/datastore.user"]
    "simulador-bqjobs"    = ["simulador", "roles/bigquery.jobUser"]
    "bqml-bqjobs"         = ["bqml", "roles/bigquery.jobUser"]
    "bqml-vertex"         = ["bqml", "roles/aiplatform.user"]
    "corte-facturacion"   = ["corte", "roles/billing.projectManager"]
    "ingesta-logs"        = ["ingesta", "roles/logging.logWriter"]
    "tablero-logs"        = ["tablero", "roles/logging.logWriter"]
    "simulador-logs"      = ["simulador", "roles/logging.logWriter"]
    "construccion-logs"   = ["construccion", "roles/logging.logWriter"]
    "despliegue-run"      = ["despliegue", "roles/run.developer"]
    "despliegue-build"    = ["despliegue", "roles/cloudbuild.builds.editor"]
    "despliegue-uso"      = ["despliegue", "roles/serviceusage.serviceUsageConsumer"]
  }
}

resource "google_project_iam_member" "p" {
  for_each = local.roles_proyecto
  project  = var.proyecto
  role     = each.value[1]
  member   = "serviceAccount:${google_service_account.sa[each.value[0]].email}"
}

# --- permisos acotados a recursos
resource "google_pubsub_topic_iam_member" "ingesta_publica" {
  for_each = toset([google_pubsub_topic.telemetria.name, google_pubsub_topic.rechazos.name])
  topic    = each.value
  role     = "roles/pubsub.publisher"
  member   = "serviceAccount:${google_service_account.sa["ingesta"].email}"
}

resource "google_bigquery_dataset_iam_member" "lectores" {
  for_each   = toset(["tablero"])
  dataset_id = google_bigquery_dataset.eco.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.sa[each.value].email}"
}

resource "google_bigquery_dataset_iam_member" "escritores" {
  for_each   = toset(["simulador", "bqml"])
  dataset_id = google_bigquery_dataset.eco.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.sa[each.value].email}"
}

# El agente de servicio de Pub/Sub escribe en BigQuery (suscripciones BigQuery) y firma los push.
locals {
  agente_pubsub = "serviceAccount:service-${data.google_project.p.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_bigquery_dataset_iam_member" "pubsub_escribe" {
  dataset_id = google_bigquery_dataset.eco.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = local.agente_pubsub
}

resource "google_service_account_iam_member" "pubsub_firma_push" {
  service_account_id = google_service_account.sa["pubsub-push"].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = local.agente_pubsub
}

# Las consultas programadas actúan como la cuenta «bqml»: el servicio de transferencias necesita poder usarla.
resource "google_service_account_iam_member" "dts_usa_bqml" {
  service_account_id = google_service_account.sa["bqml"].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:service-${data.google_project.p.number}@gcp-sa-bigquerydatatransfer.iam.gserviceaccount.com"
  depends_on         = [google_project_service_identity.agentes]
}

resource "google_artifact_registry_repository_iam_member" "construccion_escribe" {
  repository = google_artifact_registry_repository.imagenes.name
  location   = var.region
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.sa["construccion"].email}"
}

resource "google_storage_bucket_iam_member" "construccion_lee_fuente" {
  for_each = toset(["construccion", "despliegue"])
  bucket   = google_storage_bucket.construccion.name
  role     = "roles/storage.objectAdmin"
  member   = "serviceAccount:${google_service_account.sa[each.value].email}"
}

# GitHub Actions puede «actuar como» las cuentas de ejecución al desplegar y lanzar builds.
resource "google_service_account_iam_member" "despliegue_actua_como" {
  for_each           = toset(["ingesta", "tablero", "simulador", "corte", "construccion"])
  service_account_id = google_service_account.sa[each.value].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.sa["despliegue"].email}"
}

# ---------------------------------------------------------------- Workload Identity Federation
# GitHub Actions obtiene credenciales de corta duración sin guardar ninguna clave en GitHub.
resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
  depends_on                = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
    "attribute.repo_id"    = "assertion.repository_id"
  }
  # Sólo la rama main de ESTE repositorio puede desplegar. Se exige también el ID numérico del repositorio:
  # si el repositorio se borrara o renombrara, otra persona podría crear uno con el mismo nombre, pero nunca
  # con el mismo ID. Los eventos de pull request (incluidos los de forks) no tienen ref main.
  attribute_condition = "assertion.repository_id == '${var.repo_github_id}' && assertion.repository == '${var.repo_github}' && assertion.ref == 'refs/heads/main' && assertion.event_name in ['push', 'workflow_dispatch']"
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "github_usa_despliegue" {
  service_account_id = google_service_account.sa["despliegue"].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repo_id/${var.repo_github_id}"
}

# «gcloud builds submit» primero consulta los metadatos del bucket (storage.buckets.get).
resource "google_storage_bucket_iam_member" "despliegue_ve_bucket" {
  bucket = google_storage_bucket.construccion.name
  role   = "roles/storage.legacyBucketReader"
  member = "serviceAccount:${google_service_account.sa["despliegue"].email}"
}

# «gcloud run deploy» verifica que la imagen exista antes de desplegar.
resource "google_artifact_registry_repository_iam_member" "despliegue_lee" {
  repository = google_artifact_registry_repository.imagenes.name
  location   = var.region
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.sa["despliegue"].email}"
}
