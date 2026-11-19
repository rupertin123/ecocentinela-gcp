# Protección de costos (presupuesto + corte automático) y monitoreo.

# ---------------------------------------------------------------- presupuesto
resource "google_pubsub_topic" "presupuesto" {
  name       = "alertas-presupuesto"
  depends_on = [google_project_service.apis]
}

resource "google_billing_budget" "tope" {
  billing_account = var.cuenta_facturacion
  display_name    = "EcoCentinela - tope ${var.presupuesto} ${var.moneda}"
  budget_filter {
    projects = ["projects/${data.google_project.p.number}"]
    # Qué se mide: el costo que se pagaría DESPUÉS de la prueba gratuita.
    # * Se descuentan el nivel gratuito permanente (FREE_TIER) y los descuentos automáticos.
    # * NO se descuenta el crédito promocional de la prueba (PROMOTION): si no, el costo sería siempre 0.
    # Lección del 15/11/2026: con EXCLUDE_ALL_CREDITS el presupuesto contaba como gasto el uso que entra en
    # el nivel gratuito (USD 1,24 «brutos» contra USD 0,19 reales en medio mes) y disparó la alerta del 90 %.
    credit_types_treatment = "INCLUDE_SPECIFIED_CREDITS"
    credit_types           = ["FREE_TIER", "SUSTAINED_USAGE_DISCOUNT", "DISCOUNT"]
    calendar_period        = "MONTH"
  }
  amount {
    specified_amount {
      currency_code = var.moneda
      units         = tostring(var.presupuesto)
    }
  }
  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.9
  }
  threshold_rules {
    threshold_percent = 1.0
  }
  all_updates_rule {
    pubsub_topic                     = google_pubsub_topic.presupuesto.id
    monitoring_notification_channels = [google_monitoring_notification_channel.correo.id]
    disable_default_iam_recipients   = false
  }
}

resource "google_pubsub_subscription" "presupuesto_a_corte" {
  name                 = "presupuesto-a-corte"
  topic                = google_pubsub_topic.presupuesto.id
  ack_deadline_seconds = 60
  push_config {
    push_endpoint = "${google_cloud_run_v2_service.corte.uri}/"
    oidc_token {
      service_account_email = google_service_account.sa["pubsub-push"].email
    }
  }
  depends_on = [google_service_account_iam_member.pubsub_firma_push]
}

# ---------------------------------------------------------------- monitoreo
resource "google_monitoring_notification_channel" "correo" {
  display_name = "Correo de alertas"
  type         = "email"
  labels = {
    email_address = var.correo_alertas
  }
  depends_on = [google_project_service.apis]
}

resource "google_monitoring_uptime_check_config" "tablero" {
  display_name = "Tablero responde"
  timeout      = "10s"
  period       = "900s" # cada 15 min alcanza y gasta poco
  http_check {
    path         = "/salud"
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }
  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.proyecto
      host       = trimprefix(google_cloud_run_v2_service.tablero.uri, "https://")
    }
  }
}

resource "google_monitoring_alert_policy" "tablero_caido" {
  display_name = "Tablero caido"
  combiner     = "OR"
  conditions {
    display_name = "La verificación de disponibilidad falla"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type=\"uptime_url\" AND metric.label.check_id=\"${google_monitoring_uptime_check_config.tablero.uptime_check_id}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "1800s"
      aggregations {
        alignment_period     = "1200s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.*"]
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.correo.id]
}

# Métricas a partir de los registros estructurados que escribe la ingesta.
resource "google_logging_metric" "uplinks" {
  name        = "eco/uplinks_aceptados"
  description = "Uplinks LoRaWAN aceptados por la ingesta"
  filter      = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"ingesta\" AND jsonPayload.message=\"uplink aceptado\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_logging_metric" "rechazos" {
  name        = "eco/eventos_rechazados"
  description = "Eventos rechazados por calidad de datos"
  filter      = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"ingesta\" AND jsonPayload.message=\"evento rechazado\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_monitoring_alert_policy" "sin_datos" {
  display_name = "La flota dejo de transmitir"
  # Apagada el 16/11/2026: mandaba un correo cada vez que el simulador no transmitía (falsas alarmas).
  enabled  = false
  combiner = "OR"
  conditions {
    # Los nodos sólo fotografían de 7 a 20 h: de noche hay 11 h sin datos por diseño, no es una falla.
    display_name = "Sin uplinks aceptados durante 13 h"
    condition_absent {
      filter   = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.uplinks.name}\" AND resource.type=\"cloud_run_revision\""
      duration = "46800s"
      aggregations {
        alignment_period   = "3600s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  # Sin canal: al apagarla, Google cerraba los incidentes viejos y mandaba un correo «Alerta recuperada» por cada uno.
  notification_channels = []
}

resource "google_monitoring_alert_policy" "errores_ingesta" {
  display_name = "Errores 5xx en la ingesta"
  combiner     = "OR"
  conditions {
    display_name = "Más de 5 respuestas 5xx en 10 min"
    condition_threshold {
      filter          = "metric.type=\"run.googleapis.com/request_count\" AND resource.type=\"cloud_run_revision\" AND resource.label.service_name=\"ingesta\" AND metric.label.response_code_class=\"5xx\""
      comparison      = "COMPARISON_GT"
      threshold_value = 5
      duration        = "0s"
      aggregations {
        alignment_period   = "600s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.correo.id]
}

resource "google_monitoring_dashboard" "operacion" {
  dashboard_json = templatefile("${path.module}/tablero_monitoreo.json", {
    uplinks  = google_logging_metric.uplinks.name
    rechazos = google_logging_metric.rechazos.name
  })
}
