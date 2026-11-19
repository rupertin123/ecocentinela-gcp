#!/usr/bin/env bash
# Apagado programado del clúster GKE de la demo (pedido de Lucas 19/11: «desplegar ~24 h y bajar»).
# Lo corre cron cada hora; actúa recién desde DESDE y, cuando termina bien, se quita del crontab.
set -uo pipefail
DESDE="2026-11-20T20:00:00Z"
LOG="$HOME/ecocentinela-gcp/k8s/gke/apagado.log"
export PATH="$HOME/google-cloud-sdk/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin" USE_GKE_GCLOUD_AUTH_PLUGIN=True
[ "$(date -u +%s)" -lt "$(date -u -d "$DESDE" +%s)" ] && exit 0
{
  echo "== $(date -u) apagando GKE"
  if "$HOME/ecocentinela-gcp/k8s/gke/apagar.sh" ecocentinela-rio-cuarto; then
    if ! gcloud container clusters list --project ecocentinela-rio-cuarto --format='value(name)' | grep -q .; then
      echo "== OK: no quedan clústeres; me saco del crontab"
      crontab -l | grep -v 'k8s/gke/apagar_cron.sh' | crontab -
    fi
  else
    echo "== falló; reintento en la próxima hora"
  fi
} >> "$LOG" 2>&1
