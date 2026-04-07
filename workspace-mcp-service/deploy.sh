#!/bin/bash
# Deploy google-workspace-mcp as a permanent Cloud Run service.
# Run from REPO ROOT: bash workspace-mcp-service/deploy.sh

set -e

PROJECT=genaiacademy-491713
REGION=us-central1
SERVICE=workspace-mcp-til
REPO=cloud-run-source-deploy
IMAGE=$REGION-docker.pkg.dev/$PROJECT/$REPO/$SERVICE

# ── Load values from .env ─────────────────────────────────────
source .env

echo "Ensuring Artifact Registry repo exists..."
gcloud artifacts repositories create $REPO \
  --repository-format=docker \
  --location=$REGION \
  --project=$PROJECT 2>/dev/null || echo "Repo already exists, continuing."

echo "Storing refresh token in Secret Manager..."
echo -n "$GOOGLE_WORKSPACE_REFRESH_TOKEN" | \
  gcloud secrets create workspace-mcp-refresh-token \
    --project=$PROJECT \
    --data-file=- 2>/dev/null || \
  echo -n "$GOOGLE_WORKSPACE_REFRESH_TOKEN" | \
  gcloud secrets versions add workspace-mcp-refresh-token \
    --project=$PROJECT \
    --data-file=-

echo "Building and pushing container from workspace-mcp-service/..."
gcloud builds submit workspace-mcp-service/ \
  --project=$PROJECT \
  --tag=$IMAGE

echo "Deploying to Cloud Run..."
gcloud run deploy $SERVICE \
  --project=$PROJECT \
  --region=$REGION \
  --image=$IMAGE \
  --platform=managed \
  --allow-unauthenticated \
  --min-instances=1 \
  --max-instances=3 \
  --memory=512Mi \
  --cpu=1 \
  --port=8080 \
  --set-env-vars="\
GOOGLE_OAUTH_CLIENT_ID=$GOOGLE_WORKSPACE_CLIENT_ID,\
GOOGLE_OAUTH_CLIENT_SECRET=$GOOGLE_WORKSPACE_CLIENT_SECRET,\
MCP_SINGLE_USER_MODE=true" \
  --set-secrets="GOOGLE_OAUTH_REFRESH_TOKEN=workspace-mcp-refresh-token:latest"

echo ""
echo "Deployment complete. workspace-mcp URL:"
gcloud run services describe $SERVICE \
  --project=$PROJECT \
  --region=$REGION \
  --format="value(status.url)"
echo ""
echo "Copy the URL above and set WORKSPACE_MCP_URL=<URL>/mcp in your .env"
