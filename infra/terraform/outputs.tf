output "url_tablero" {
  value = google_cloud_run_v2_service.tablero.uri
}

output "url_ingesta" {
  value = google_cloud_run_v2_service.ingesta.uri
}

output "proveedor_wif" {
  description = "Va en el secreto WIF_PROVIDER del repositorio de GitHub"
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "cuenta_despliegue" {
  value = google_service_account.sa["despliegue"].email
}

output "cuenta_construccion" {
  value = google_service_account.sa["construccion"].email
}

output "bucket_construccion" {
  value = google_storage_bucket.construccion.name
}
