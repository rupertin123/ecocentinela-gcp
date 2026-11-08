# Datos: Pub/Sub -> BigQuery (historia) y Firestore (último estado de cada nodo).

# ---------------------------------------------------------------- BigQuery
resource "google_bigquery_dataset" "eco" {
  dataset_id    = "ecocentinela"
  location      = "US"
  description   = "Telemetría de la flota simulada de EcoCentinela (Río Cuarto)"
  friendly_name = "EcoCentinela"
  depends_on    = [google_project_service.apis]
}

resource "google_bigquery_table" "telemetria" {
  dataset_id          = google_bigquery_dataset.eco.dataset_id
  table_id            = "telemetria"
  description         = "Una fila por uplink LoRaWAN aceptado. Particionada por día y agrupada por nodo para que cada consulta lea poco (y cueste poco)."
  deletion_protection = false
  time_partitioning {
    type          = "DAY"
    field         = "medido"
    expiration_ms = 180 * 24 * 3600 * 1000 # 180 días: el almacenamiento queda muy por debajo de los 10 GiB gratis
  }
  clustering = ["nodo_id"]
  schema     = file("${path.module}/esquemas/telemetria.json")
}

resource "google_bigquery_table" "rechazos" {
  dataset_id          = google_bigquery_dataset.eco.dataset_id
  table_id            = "rechazos"
  description         = "Eventos que no pasaron la validación de calidad de datos en la ingesta."
  deletion_protection = false
  time_partitioning {
    type          = "DAY"
    field         = "recibido"
    expiration_ms = 90 * 24 * 3600 * 1000
  }
  schema = jsonencode([
    { name = "motivo", type = "STRING", mode = "REQUIRED" },
    { name = "evento", type = "STRING", mode = "NULLABLE" },
    { name = "recibido", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "telemetria_limpia" {
  dataset_id          = google_bigquery_dataset.eco.dataset_id
  table_id            = "telemetria_limpia"
  description         = "Vista sin duplicados (Pub/Sub entrega «al menos una vez») y sin la prueba de carga."
  deletion_protection = false
  view {
    use_legacy_sql = false
    query          = templatefile("${path.module}/../../sql/vista_telemetria_limpia.sql", { p = var.proyecto })
  }
  depends_on = [google_bigquery_table.telemetria]
}

resource "google_bigquery_table" "calidad" {
  dataset_id          = google_bigquery_dataset.eco.dataset_id
  table_id            = "calidad_diaria"
  description         = "Indicadores de calidad de datos por nodo y día: completitud, duplicados, rechazos."
  deletion_protection = false
  view {
    use_legacy_sql = false
    query          = templatefile("${path.module}/../../sql/vista_calidad_diaria.sql", { p = var.proyecto })
  }
  depends_on = [google_bigquery_table.telemetria, google_bigquery_table.rechazos]
}

# Reentrenamiento diario del pronóstico (BigQuery ML ARIMA_PLUS) con una consulta programada.
resource "google_bigquery_data_transfer_config" "pronostico" {
  display_name         = "Pronostico diario de mosca blanca (ARIMA_PLUS)"
  location             = "US"
  data_source_id       = "scheduled_query"
  schedule             = "every day 09:00" # 06:00 en Argentina
  service_account_name = google_service_account.sa["bqml"].email
  params = {
    query = templatefile("${path.module}/../../sql/entrenar_y_pronosticar.sql", { p = var.proyecto })
  }
  depends_on = [google_service_account_iam_member.dts_usa_bqml, google_bigquery_dataset_iam_member.escritores]
}

# ---------------------------------------------------------------- Pub/Sub
resource "google_pubsub_topic" "telemetria" {
  name                       = "telemetria"
  message_retention_duration = "86400s"
  depends_on                 = [google_project_service.apis]
}

resource "google_pubsub_topic" "rechazos" {
  name       = "telemetria-rechazada"
  depends_on = [google_project_service.apis]
}

resource "google_pubsub_topic" "no_entregables" {
  name       = "telemetria-no-entregable"
  depends_on = [google_project_service.apis]
}

# Suscripción BigQuery: Pub/Sub escribe directo en la tabla, sin Dataflow ni código intermedio.
resource "google_pubsub_subscription" "a_bigquery" {
  name  = "telemetria-a-bigquery"
  topic = google_pubsub_topic.telemetria.id
  bigquery_config {
    table               = "${var.proyecto}.${google_bigquery_dataset.eco.dataset_id}.${google_bigquery_table.telemetria.table_id}"
    use_table_schema    = true
    drop_unknown_fields = true
  }
  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.no_entregables.id
    max_delivery_attempts = 5
  }
  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }
  depends_on = [google_bigquery_dataset_iam_member.pubsub_escribe]
}

resource "google_pubsub_subscription" "rechazos_a_bigquery" {
  name  = "rechazos-a-bigquery"
  topic = google_pubsub_topic.rechazos.id
  bigquery_config {
    table               = "${var.proyecto}.${google_bigquery_dataset.eco.dataset_id}.${google_bigquery_table.rechazos.table_id}"
    use_table_schema    = true
    drop_unknown_fields = true
  }
  depends_on = [google_bigquery_dataset_iam_member.pubsub_escribe]
}

# Para poder inspeccionar a mano lo que no se pudo escribir en BigQuery.
resource "google_pubsub_subscription" "no_entregables" {
  name                       = "telemetria-no-entregable-revision"
  topic                      = google_pubsub_topic.no_entregables.id
  message_retention_duration = "604800s"
}

# La cola de mensajes muertos necesita permiso para recibir y para sacar mensajes de la suscripción original.
resource "google_pubsub_topic_iam_member" "dlq_publica" {
  topic  = google_pubsub_topic.no_entregables.name
  role   = "roles/pubsub.publisher"
  member = local.agente_pubsub
}

resource "google_pubsub_subscription_iam_member" "dlq_suscribe" {
  subscription = google_pubsub_subscription.a_bigquery.name
  role         = "roles/pubsub.subscriber"
  member       = local.agente_pubsub
}

# ---------------------------------------------------------------- Firestore
resource "google_firestore_database" "default" {
  name                    = "(default)" # el nivel gratuito sólo aplica a la base «(default)»
  location_id             = var.region
  type                    = "FIRESTORE_NATIVE"
  delete_protection_state = "DELETE_PROTECTION_DISABLED"
  deletion_policy         = "DELETE"
  depends_on              = [google_project_service.apis]
}
