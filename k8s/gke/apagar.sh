#!/usr/bin/env bash
# Borra el clúster GKE y todo lo que creó (balanceador incluido). Cloud Run sigue funcionando igual.
set -euo pipefail
cd "$(dirname "$0")/../.."
P="${1:?proyecto}"
# Primero el Ingress: así el controlador de GKE borra el balanceador y sus reglas antes de que desaparezca el clúster.
kubectl -n ecocentinela delete ingress tablero --ignore-not-found --wait=true || true
terraform -chdir=infra/gke init -input=false -backend-config="bucket=${P}-tfstate" -backend-config="prefix=gke" >/dev/null
terraform -chdir=infra/gke destroy -input=false -auto-approve -var proyecto="$P"
echo "== restos (deberían estar vacíos):"
gcloud compute forwarding-rules list --project "$P" --format='value(name)'
gcloud compute network-endpoint-groups list --project "$P" --format='value(name)'
