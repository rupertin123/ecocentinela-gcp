variable "proyecto" {
  description = "ID del proyecto de Google Cloud"
  type        = string
}

variable "region" {
  description = "us-central1: es una de las tres regiones donde Cloud Storage y Compute Engine son gratuitos"
  type        = string
  default     = "us-central1"
}

variable "cuenta_facturacion" {
  description = "ID de la cuenta de facturación (formato XXXXXX-XXXXXX-XXXXXX)"
  type        = string
}

variable "moneda" {
  description = "Moneda de la cuenta de facturación; el presupuesto debe usar la misma"
  type        = string
  default     = "USD"
}

variable "presupuesto" {
  description = "Tope mensual de costo BRUTO (antes de créditos) en la moneda de la cuenta. Superado, se desactiva la facturación del proyecto."
  type        = number
  default     = 5
}

variable "oauth_client_id" {
  description = "ID de cliente OAuth (tipo «Aplicación web») para el botón de Google del tablero. Vacío = tablero sólo de lectura."
  type        = string
  default     = ""
}

variable "correo_alertas" {
  description = "Correo que recibe las alertas de monitoreo y presupuesto"
  type        = string
}

variable "repo_github" {
  description = "Repositorio autorizado a desplegar con Workload Identity Federation (usuario/repositorio)"
  type        = string
}

variable "repo_github_id" {
  description = "ID numérico del repositorio de GitHub (gh api repos/USUARIO/REPO --jq .id)"
  type        = string
}

variable "version_imagen" {
  description = "Etiqueta inicial de las imágenes; después la actualiza el despliegue continuo"
  type        = string
  default     = "v1"
}
