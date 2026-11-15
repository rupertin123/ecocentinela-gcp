# Cómputo: tres servicios de Cloud Run, un Cloud Run Job y su disparador.

locals {
  imagen = { for s in ["ingesta", "tablero", "simulador", "corte"] :
  s => "${local.repo_imagenes}/${s}:${var.version_imagen}" }
}

# ---------------------------------------------------------------- ingesta (privada)
resource "google_cloud_run_v2_service" "ingesta" {
  name                = "ingesta"
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"
  template {
    service_account                  = google_service_account.sa["ingesta"].email
    max_instance_request_concurrency = 40
    timeout                          = "30s"
    scaling {
      min_instance_count = 0 # escala a cero: sin tráfico no consume nada
      max_instance_count = 2
    }
    containers {
      image = local.imagen["ingesta"]
      resources {
        limits            = { cpu = "1", memory = "512Mi" }
        cpu_idle          = true # CPU sólo mientras atiende un pedido (facturación por pedido)
        startup_cpu_boost = true
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.proyecto
      }
      startup_probe {
        http_get {
          path = "/salud"
        }
      }
    }
  }
  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version] # la imagen la actualiza el CI/CD
  }
  depends_on = [google_project_iam_member.p]
}

# Sólo el simulador (y, en la vida real, el servidor ChirpStack) puede llamar a la ingesta.
resource "google_cloud_run_v2_service_iam_member" "ingesta_invocador" {
  name     = google_cloud_run_v2_service.ingesta.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.sa["simulador"].email}"
}

# ---------------------------------------------------------------- tablero (público)
resource "google_cloud_run_v2_service" "tablero" {
  name                = "tablero"
  location            = var.region
  deletion_protection = false
  template {
    service_account                  = google_service_account.sa["tablero"].email
    max_instance_request_concurrency = 80
    timeout                          = "900s" # WebSocket: el tablero corta cada sesión a los 10 min
    session_affinity                 = true
    scaling {
      min_instance_count = 0
      max_instance_count = 1 # tope duro de costo aunque alguien abuse de la página pública
    }
    containers {
      image = local.imagen["tablero"]
      resources {
        limits   = { cpu = "1", memory = "512Mi" }
        cpu_idle = true
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.proyecto
      }
      env {
        name  = "DATASET"
        value = google_bigquery_dataset.eco.dataset_id
      }
      env {
        name  = "OAUTH_CLIENT_ID" # público: identifica la app ante Google Sign-In
        value = var.oauth_client_id
      }
      env {
        name = "ADMIN_EMAILS" # correos autorizados a registrar aplicaciones (no van al repositorio)
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.admins.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "GEMINI_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.gemini.secret_id
            version = "latest"
          }
        }
      }
    }
  }
  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version]
  }
  depends_on = [google_project_iam_member.p, google_secret_manager_secret_iam_member.tablero_lee]
}

resource "google_cloud_run_v2_service_iam_member" "tablero_publico" {
  name     = google_cloud_run_v2_service.tablero.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ---------------------------------------------------------------- simulador (Job + Scheduler)
resource "google_cloud_run_v2_job" "simulador" {
  name                = "simulador"
  location            = var.region
  deletion_protection = false
  template {
    task_count = 1
    template {
      service_account = google_service_account.sa["simulador"].email
      max_retries     = 1
      timeout         = "300s"
      containers {
        image = local.imagen["simulador"]
        resources {
          limits = { cpu = "1", memory = "512Mi" }
        }
        env {
          name  = "GOOGLE_CLOUD_PROJECT"
          value = var.proyecto
        }
        env {
          name  = "URL_INGESTA"
          value = google_cloud_run_v2_service.ingesta.uri
        }
      }
    }
  }
  lifecycle {
    ignore_changes = [template[0].template[0].containers[0].image, client, client_version]
  }
}

resource "google_cloud_run_v2_job_iam_member" "programa_ejecuta" {
  name     = google_cloud_run_v2_job.simulador.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.sa["programa"].email}"
}

# Cada hora de 7 a 20 (las cámaras necesitan luz), la «flota» saca una foto y transmite.
# Cloud Scheduler: 3 trabajos gratis por cuenta de facturación.
resource "google_cloud_scheduler_job" "flota" {
  name        = "flota-cada-hora-de-dia"
  description = "Dispara una foto y una transmisión de los 12 nodos simulados"
  schedule    = "0 7-20 * * *"
  time_zone   = "America/Argentina/Cordoba"
  region      = var.region
  retry_config {
    retry_count = 0
  }
  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/${google_cloud_run_v2_job.simulador.id}:run"
    oauth_token {
      service_account_email = google_service_account.sa["programa"].email
    }
  }
  depends_on = [google_cloud_run_v2_job_iam_member.programa_ejecuta]
}

# ---------------------------------------------------------------- corte de facturación
resource "google_cloud_run_v2_service" "corte" {
  name                = "corte-facturacion"
  location            = var.region
  deletion_protection = false
  template {
    service_account = google_service_account.sa["corte"].email
    scaling {
      max_instance_count = 1
    }
    containers {
      image = local.imagen["corte"]
      resources {
        limits   = { cpu = "1", memory = "256Mi" }
        cpu_idle = true
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.proyecto
      }
    }
  }
  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version]
  }
  depends_on = [google_project_iam_member.p]
}

resource "google_cloud_run_v2_service_iam_member" "corte_invocador" {
  name     = google_cloud_run_v2_service.corte.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.sa["pubsub-push"].email}"
}

# ---------------------------------------------------------------- secreto de Gemini
# La clave es de un proyecto SIN facturación (capa gratuita de la API de Gemini).
# El valor se carga aparte con gcloud, para que nunca quede escrito en el estado de Terraform.
resource "google_secret_manager_secret" "gemini" {
  secret_id = "gemini-api-key"
  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }
  depends_on = [google_project_service.apis]
}

# Lista de correos autorizados a registrar aplicaciones (separados por coma). Mismo criterio: se carga con gcloud.
resource "google_secret_manager_secret" "admins" {
  secret_id = "admin-emails"
  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_iam_member" "tablero_lee" {
  for_each  = { gemini = google_secret_manager_secret.gemini.id, admins = google_secret_manager_secret.admins.id }
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.sa["tablero"].email}"
}

resource "google_pubsub_topic_iam_member" "tablero_publica_aplicaciones" {
  topic  = google_pubsub_topic.aplicaciones.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.sa["tablero"].email}"
}
