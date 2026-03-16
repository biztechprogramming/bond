/**
 * Script Templates — common deployment script patterns.
 *
 * Used by the frontend template selector and Quick Deploy to bootstrap
 * deployment scripts for common infrastructure patterns.
 */

export interface ScriptTemplate {
  id: string;
  label: string;
  description: string;
  strategy: string;
  script: string;
  rollback?: string;
  variables: { name: string; description: string; required: boolean }[];
}

export const SCRIPT_TEMPLATES: ScriptTemplate[] = [
  {
    id: "ssh-deploy",
    label: "SSH Deploy",
    description: "Git pull and systemd restart over SSH",
    strategy: "script",
    script: `#!/usr/bin/env bash
# meta:name: SSH Deploy
# meta:version: 1
# meta:timeout: 300
set -euo pipefail

DEPLOY_HOST="\${DEPLOY_HOST:?DEPLOY_HOST is required}"
DEPLOY_PATH="\${DEPLOY_PATH:?DEPLOY_PATH is required}"
SERVICE_NAME="\${SERVICE_NAME:?SERVICE_NAME is required}"
BRANCH="\${BRANCH:-main}"

if [[ "\${1:-}" == "--dry-run" ]]; then
  echo "[DRY RUN] Would deploy $BRANCH to $DEPLOY_HOST:$DEPLOY_PATH and restart $SERVICE_NAME"
  exit 0
fi

echo "Deploying to $DEPLOY_HOST..."
ssh "$DEPLOY_HOST" bash -s <<REMOTE
  set -euo pipefail
  cd "$DEPLOY_PATH"
  git fetch origin "$BRANCH"
  git reset --hard "origin/$BRANCH"
  sudo systemctl restart "$SERVICE_NAME"
  echo "Service restarted"
REMOTE

echo "Deploy complete"
`,
    rollback: `#!/usr/bin/env bash
# meta:name: SSH Rollback
# meta:version: 1
set -euo pipefail

DEPLOY_HOST="\${DEPLOY_HOST:?DEPLOY_HOST is required}"
DEPLOY_PATH="\${DEPLOY_PATH:?DEPLOY_PATH is required}"
SERVICE_NAME="\${SERVICE_NAME:?SERVICE_NAME is required}"

if [[ "\${1:-}" == "--dry-run" ]]; then
  echo "[DRY RUN] Would rollback $SERVICE_NAME on $DEPLOY_HOST"
  exit 0
fi

ssh "$DEPLOY_HOST" bash -s <<REMOTE
  set -euo pipefail
  cd "$DEPLOY_PATH"
  git checkout HEAD~1
  sudo systemctl restart "$SERVICE_NAME"
REMOTE

echo "Rollback complete"
`,
    variables: [
      { name: "DEPLOY_HOST", description: "SSH host (user@host)", required: true },
      { name: "DEPLOY_PATH", description: "Path to project on remote", required: true },
      { name: "SERVICE_NAME", description: "systemd service name", required: true },
      { name: "BRANCH", description: "Git branch to deploy", required: false },
    ],
  },
  {
    id: "docker-build-run",
    label: "Docker Build & Run",
    description: "Build Docker image, stop old container, run new",
    strategy: "dockerfile",
    script: `#!/usr/bin/env bash
# meta:name: Docker Build & Run
# meta:version: 1
# meta:timeout: 600
set -euo pipefail

IMAGE_NAME="\${IMAGE_NAME:?IMAGE_NAME is required}"
CONTAINER_NAME="\${CONTAINER_NAME:?CONTAINER_NAME is required}"
PORT="\${PORT:-3000}"
REPO_URL="\${REPO_URL:?REPO_URL is required}"
BRANCH="\${BRANCH:-main}"

if [[ "\${1:-}" == "--dry-run" ]]; then
  echo "[DRY RUN] Would build $IMAGE_NAME and run as $CONTAINER_NAME on port $PORT"
  exit 0
fi

REPO_DIR="/tmp/bond-deploy-$(echo "$IMAGE_NAME" | tr '/' '-')"

if [[ -d "$REPO_DIR/.git" ]]; then
  cd "$REPO_DIR" && git fetch origin "$BRANCH" && git reset --hard "origin/$BRANCH"
else
  git clone --branch "$BRANCH" "$REPO_URL" "$REPO_DIR" && cd "$REPO_DIR"
fi

docker build -t "$IMAGE_NAME:latest" .
docker stop "$CONTAINER_NAME" 2>/dev/null || true
docker rm "$CONTAINER_NAME" 2>/dev/null || true
docker run -d --name "$CONTAINER_NAME" -p "$PORT:$PORT" --restart unless-stopped "$IMAGE_NAME:latest"

echo "Deploy complete — $CONTAINER_NAME running on port $PORT"
`,
    rollback: `#!/usr/bin/env bash
# meta:name: Docker Rollback
# meta:version: 1
set -euo pipefail

IMAGE_NAME="\${IMAGE_NAME:?IMAGE_NAME is required}"
CONTAINER_NAME="\${CONTAINER_NAME:?CONTAINER_NAME is required}"
PORT="\${PORT:-3000}"

if [[ "\${1:-}" == "--dry-run" ]]; then
  echo "[DRY RUN] Would rollback $CONTAINER_NAME to previous image"
  exit 0
fi

docker stop "$CONTAINER_NAME" 2>/dev/null || true
docker rm "$CONTAINER_NAME" 2>/dev/null || true

if docker image inspect "$IMAGE_NAME:previous" &>/dev/null; then
  docker run -d --name "$CONTAINER_NAME" -p "$PORT:$PORT" --restart unless-stopped "$IMAGE_NAME:previous"
  echo "Rollback complete"
else
  echo "No previous image found"
  exit 1
fi
`,
    variables: [
      { name: "IMAGE_NAME", description: "Docker image name", required: true },
      { name: "CONTAINER_NAME", description: "Container name", required: true },
      { name: "REPO_URL", description: "Git repository URL", required: true },
      { name: "PORT", description: "Port to expose", required: false },
      { name: "BRANCH", description: "Git branch", required: false },
    ],
  },
  {
    id: "docker-compose",
    label: "Docker Compose",
    description: "Docker Compose down/up with build",
    strategy: "docker-compose",
    script: `#!/usr/bin/env bash
# meta:name: Docker Compose Deploy
# meta:version: 1
# meta:timeout: 600
set -euo pipefail

REPO_URL="\${REPO_URL:?REPO_URL is required}"
BRANCH="\${BRANCH:-main}"
COMPOSE_FILE="\${COMPOSE_FILE:-docker-compose.yml}"

if [[ "\${1:-}" == "--dry-run" ]]; then
  echo "[DRY RUN] Would deploy using docker compose ($COMPOSE_FILE)"
  exit 0
fi

REPO_DIR="/tmp/bond-deploy-compose"

if [[ -d "$REPO_DIR/.git" ]]; then
  cd "$REPO_DIR" && git fetch origin "$BRANCH" && git reset --hard "origin/$BRANCH"
else
  git clone --branch "$BRANCH" "$REPO_URL" "$REPO_DIR" && cd "$REPO_DIR"
fi

docker compose -f "$COMPOSE_FILE" down || true
docker compose -f "$COMPOSE_FILE" up -d --build

echo "Deploy complete"
`,
    rollback: `#!/usr/bin/env bash
# meta:name: Docker Compose Rollback
# meta:version: 1
set -euo pipefail

COMPOSE_FILE="\${COMPOSE_FILE:-docker-compose.yml}"

if [[ "\${1:-}" == "--dry-run" ]]; then
  echo "[DRY RUN] Would rollback docker compose deployment"
  exit 0
fi

REPO_DIR="/tmp/bond-deploy-compose"
cd "$REPO_DIR" 2>/dev/null || { echo "Repo dir not found"; exit 1; }
docker compose -f "$COMPOSE_FILE" down
git checkout HEAD~1
docker compose -f "$COMPOSE_FILE" up -d --build

echo "Rollback complete"
`,
    variables: [
      { name: "REPO_URL", description: "Git repository URL", required: true },
      { name: "BRANCH", description: "Git branch", required: false },
      { name: "COMPOSE_FILE", description: "Compose file path", required: false },
    ],
  },
  {
    id: "db-migration",
    label: "Database Migration",
    description: "Run SQL migration with backup",
    strategy: "script",
    script: `#!/usr/bin/env bash
# meta:name: Database Migration
# meta:version: 1
# meta:timeout: 900
set -euo pipefail

DB_HOST="\${DB_HOST:?DB_HOST is required}"
DB_NAME="\${DB_NAME:?DB_NAME is required}"
DB_USER="\${DB_USER:?DB_USER is required}"
MIGRATION_DIR="\${MIGRATION_DIR:?MIGRATION_DIR is required}"

if [[ "\${1:-}" == "--dry-run" ]]; then
  echo "[DRY RUN] Would run migrations from $MIGRATION_DIR on $DB_NAME@$DB_HOST"
  echo "Migration files:"
  ls -1 "$MIGRATION_DIR"/*.sql 2>/dev/null || echo "  (none found)"
  exit 0
fi

BACKUP_FILE="/tmp/bond-backup-$DB_NAME-$(date +%Y%m%d%H%M%S).sql"
echo "Creating backup: $BACKUP_FILE"
pg_dump -h "$DB_HOST" -U "$DB_USER" "$DB_NAME" > "$BACKUP_FILE"

echo "Running migrations..."
for f in "$MIGRATION_DIR"/*.sql; do
  echo "  Applying: $(basename "$f")"
  psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -f "$f" || {
    echo "Migration failed! Restoring backup..."
    psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" < "$BACKUP_FILE"
    echo "Backup restored"
    exit 1
  }
done

echo "All migrations applied successfully"
`,
    rollback: `#!/usr/bin/env bash
# meta:name: Database Rollback
# meta:version: 1
set -euo pipefail

DB_HOST="\${DB_HOST:?DB_HOST is required}"
DB_NAME="\${DB_NAME:?DB_NAME is required}"
DB_USER="\${DB_USER:?DB_USER is required}"

if [[ "\${1:-}" == "--dry-run" ]]; then
  echo "[DRY RUN] Would restore latest backup for $DB_NAME"
  exit 0
fi

BACKUP_FILE=$(ls -t /tmp/bond-backup-$DB_NAME-*.sql 2>/dev/null | head -1)
if [[ -z "$BACKUP_FILE" ]]; then
  echo "No backup found"
  exit 1
fi

echo "Restoring from: $BACKUP_FILE"
psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" < "$BACKUP_FILE"
echo "Rollback complete"
`,
    variables: [
      { name: "DB_HOST", description: "Database host", required: true },
      { name: "DB_NAME", description: "Database name", required: true },
      { name: "DB_USER", description: "Database user", required: true },
      { name: "MIGRATION_DIR", description: "Directory containing .sql files", required: true },
    ],
  },
  {
    id: "aws-ecs",
    label: "AWS ECS Update",
    description: "Update ECS service with new image",
    strategy: "script",
    script: `#!/usr/bin/env bash
# meta:name: AWS ECS Update
# meta:version: 1
# meta:timeout: 600
set -euo pipefail

CLUSTER="\${ECS_CLUSTER:?ECS_CLUSTER is required}"
SERVICE="\${ECS_SERVICE:?ECS_SERVICE is required}"
IMAGE="\${IMAGE:?IMAGE is required}"
REGION="\${AWS_REGION:-us-east-1}"

if [[ "\${1:-}" == "--dry-run" ]]; then
  echo "[DRY RUN] Would update ECS service $SERVICE in cluster $CLUSTER with image $IMAGE"
  exit 0
fi

echo "Getting current task definition..."
TASK_DEF=$(aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --region "$REGION" \
  --query 'services[0].taskDefinition' --output text)

echo "Registering new task definition with image: $IMAGE"
NEW_TASK_DEF=$(aws ecs describe-task-definition --task-definition "$TASK_DEF" --region "$REGION" \
  --query 'taskDefinition' | \
  jq --arg IMG "$IMAGE" '.containerDefinitions[0].image = $IMG | del(.taskDefinitionArn, .revision, .status, .requiresAttributes, .compatibilities, .registeredAt, .registeredBy)')

NEW_ARN=$(echo "$NEW_TASK_DEF" | aws ecs register-task-definition --cli-input-json file:///dev/stdin --region "$REGION" \
  --query 'taskDefinition.taskDefinitionArn' --output text)

echo "Updating service to use: $NEW_ARN"
aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --task-definition "$NEW_ARN" --region "$REGION" > /dev/null

echo "Waiting for deployment to stabilize..."
aws ecs wait services-stable --cluster "$CLUSTER" --services "$SERVICE" --region "$REGION"

echo "Deploy complete"
`,
    variables: [
      { name: "ECS_CLUSTER", description: "ECS cluster name", required: true },
      { name: "ECS_SERVICE", description: "ECS service name", required: true },
      { name: "IMAGE", description: "Docker image URI with tag", required: true },
      { name: "AWS_REGION", description: "AWS region", required: false },
    ],
  },
  {
    id: "kubernetes-rollout",
    label: "Kubernetes Rollout",
    description: "kubectl set image and wait for rollout",
    strategy: "script",
    script: `#!/usr/bin/env bash
# meta:name: Kubernetes Rollout
# meta:version: 1
# meta:timeout: 600
set -euo pipefail

DEPLOYMENT="\${K8S_DEPLOYMENT:?K8S_DEPLOYMENT is required}"
CONTAINER="\${K8S_CONTAINER:?K8S_CONTAINER is required}"
IMAGE="\${IMAGE:?IMAGE is required}"
NAMESPACE="\${K8S_NAMESPACE:-default}"

if [[ "\${1:-}" == "--dry-run" ]]; then
  echo "[DRY RUN] Would set $DEPLOYMENT/$CONTAINER to $IMAGE in namespace $NAMESPACE"
  kubectl rollout status "deployment/$DEPLOYMENT" -n "$NAMESPACE" 2>/dev/null || true
  exit 0
fi

echo "Updating deployment $DEPLOYMENT..."
kubectl set image "deployment/$DEPLOYMENT" "$CONTAINER=$IMAGE" -n "$NAMESPACE"

echo "Waiting for rollout..."
kubectl rollout status "deployment/$DEPLOYMENT" -n "$NAMESPACE" --timeout=300s

echo "Deploy complete"
`,
    rollback: `#!/usr/bin/env bash
# meta:name: Kubernetes Rollback
# meta:version: 1
set -euo pipefail

DEPLOYMENT="\${K8S_DEPLOYMENT:?K8S_DEPLOYMENT is required}"
NAMESPACE="\${K8S_NAMESPACE:-default}"

if [[ "\${1:-}" == "--dry-run" ]]; then
  echo "[DRY RUN] Would rollback deployment $DEPLOYMENT"
  exit 0
fi

kubectl rollout undo "deployment/$DEPLOYMENT" -n "$NAMESPACE"
kubectl rollout status "deployment/$DEPLOYMENT" -n "$NAMESPACE" --timeout=300s

echo "Rollback complete"
`,
    variables: [
      { name: "K8S_DEPLOYMENT", description: "Kubernetes deployment name", required: true },
      { name: "K8S_CONTAINER", description: "Container name in the deployment", required: true },
      { name: "IMAGE", description: "Docker image URI with tag", required: true },
      { name: "K8S_NAMESPACE", description: "Kubernetes namespace", required: false },
    ],
  },
  {
    id: "static-site-s3",
    label: "Static Site (S3)",
    description: "Sync to S3 and invalidate CloudFront",
    strategy: "script",
    script: `#!/usr/bin/env bash
# meta:name: Static Site S3 Deploy
# meta:version: 1
# meta:timeout: 300
set -euo pipefail

S3_BUCKET="\${S3_BUCKET:?S3_BUCKET is required}"
BUILD_DIR="\${BUILD_DIR:-dist}"
CLOUDFRONT_ID="\${CLOUDFRONT_ID:-}"
REGION="\${AWS_REGION:-us-east-1}"
REPO_URL="\${REPO_URL:?REPO_URL is required}"
BRANCH="\${BRANCH:-main}"
BUILD_CMD="\${BUILD_CMD:-npm ci && npm run build}"

if [[ "\${1:-}" == "--dry-run" ]]; then
  echo "[DRY RUN] Would build and sync to s3://$S3_BUCKET"
  [[ -n "$CLOUDFRONT_ID" ]] && echo "  Would invalidate CloudFront distribution $CLOUDFRONT_ID"
  exit 0
fi

REPO_DIR="/tmp/bond-deploy-static"
if [[ -d "$REPO_DIR/.git" ]]; then
  cd "$REPO_DIR" && git fetch origin "$BRANCH" && git reset --hard "origin/$BRANCH"
else
  git clone --branch "$BRANCH" "$REPO_URL" "$REPO_DIR" && cd "$REPO_DIR"
fi

echo "Building..."
eval "$BUILD_CMD"

echo "Syncing to s3://$S3_BUCKET..."
aws s3 sync "$BUILD_DIR" "s3://$S3_BUCKET" --delete --region "$REGION"

if [[ -n "$CLOUDFRONT_ID" ]]; then
  echo "Invalidating CloudFront cache..."
  aws cloudfront create-invalidation --distribution-id "$CLOUDFRONT_ID" --paths "/*" > /dev/null
fi

echo "Deploy complete"
`,
    variables: [
      { name: "S3_BUCKET", description: "S3 bucket name", required: true },
      { name: "REPO_URL", description: "Git repository URL", required: true },
      { name: "BUILD_DIR", description: "Build output directory", required: false },
      { name: "CLOUDFRONT_ID", description: "CloudFront distribution ID", required: false },
      { name: "BUILD_CMD", description: "Build command", required: false },
      { name: "BRANCH", description: "Git branch", required: false },
      { name: "AWS_REGION", description: "AWS region", required: false },
    ],
  },
];
