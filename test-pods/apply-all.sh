#!/bin/bash
# Deploy all test pods to the agent-test namespace.
# Usage: bash apply-all.sh

set -e

echo "Creating namespace..."
kubectl apply -f 00-namespace.yaml

echo "Deploying test pods..."
kubectl apply -f 01-image-pull-backoff.yaml
kubectl apply -f 02-crash-loop-backoff.yaml
kubectl apply -f 03-oom-killed.yaml
kubectl apply -f 04-config-error.yaml
kubectl apply -f 05-pending-unschedulable.yaml

echo ""
echo "Done. Waiting a few seconds for states to settle..."
sleep 5

echo ""
kubectl get pods -n agent-test
echo ""
echo "Run the agent against these pods:"
echo "  cd .. && python main.py --once"
echo ""
echo "To clean up everything:"
echo "  kubectl delete namespace agent-test"
