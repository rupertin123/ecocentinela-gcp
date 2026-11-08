#!/usr/bin/env bash
# Levanta un clúster Kubernetes local (kind), despliega EcoCentinela y corre una prueba de carga.
set -euo pipefail
cd "$(dirname "$0")/.."
for s in ingesta simulador; do docker build -q -f services/$s/Dockerfile -t eco-$s:local . ; done
kind get clusters | grep -q '^eco$' || kind create cluster --name eco --wait 120s
kind load docker-image eco-ingesta:local eco-simulador:local --name eco
kubectl apply -f k8s/ecocentinela.yaml
kubectl -n ecocentinela rollout status deploy/ingesta --timeout=180s
kubectl -n ecocentinela delete job prueba-carga --ignore-not-found
kubectl apply -f k8s/ecocentinela.yaml
kubectl -n ecocentinela wait --for=condition=complete job/prueba-carga --timeout=300s
kubectl -n ecocentinela logs job/prueba-carga | tail -1
kubectl -n ecocentinela get pods -o wide
