#!/usr/bin/env bash
# Crea el clúster GKE Autopilot (Terraform), despliega EcoCentinela y corre la prueba de carga interna.
# Uso: k8s/gke/desplegar.sh <proyecto> [version_imagen]     Apagar todo: k8s/gke/apagar.sh <proyecto>
set -euo pipefail
cd "$(dirname "$0")/../.."
P="${1:?proyecto}"
V="${2:-$(gcloud run services describe ingesta --region us-central1 --project "$P" --format='value(spec.template.spec.containers[0].image)' | sed 's/.*://')}"
echo "== proyecto $P, imágenes :$V"

terraform -chdir=infra/gke init -input=false -backend-config="bucket=${P}-tfstate" -backend-config="prefix=gke" >/dev/null
terraform -chdir=infra/gke apply -input=false -auto-approve -var proyecto="$P"
gcloud container clusters get-credentials ecocentinela --region us-central1 --project "$P"

render() { sed -e "s/__PROYECTO__/$P/g" -e "s/__VERSION__/$V/g" "$1"; }
render k8s/gke/ecocentinela-gke.yaml | kubectl apply -f -

# El secreto se copia desde Secret Manager en el momento: nunca queda en el repositorio.
kubectl -n ecocentinela create secret generic tablero-secretos \
  --from-literal=GEMINI_API_KEY="$(gcloud secrets versions access latest --secret=gemini-api-key --project "$P")" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n ecocentinela rollout status deploy/ingesta --timeout=600s
kubectl -n ecocentinela rollout status deploy/tablero --timeout=600s

kubectl -n ecocentinela delete job prueba-carga --ignore-not-found
render k8s/gke/prueba-carga.yaml | kubectl apply -f -
kubectl -n ecocentinela wait --for=condition=complete job/prueba-carga --timeout=600s
kubectl -n ecocentinela logs job/prueba-carga | tail -n 3

echo "== esperando la IP pública del balanceador (puede tardar 5-10 min)"
for _ in $(seq 1 90); do
  IP=$(kubectl -n ecocentinela get ingress tablero -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)
  [ -n "$IP" ] && curl -fsS -m 5 "http://$IP/salud" >/dev/null 2>&1 && break
  sleep 10
done
echo "== tablero en GKE: http://${IP:-pendiente}/"
kubectl -n ecocentinela get pods,svc,ingress,hpa -o wide
