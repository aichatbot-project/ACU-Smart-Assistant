#!/usr/bin/env bash
# Örnek: aynı backend task definition ile tek seferlik migrate.
# Aşağıdaki değişkenleri doldurun; subnet ve güvenlik grubu genelde özel (RDS’e erişebilen).
set -euo pipefail

: "${AWS_REGION:?}"
: "${ECS_CLUSTER:?}"
: "${SUBNET_IDS:?}"          # örn. subnet-aaa,subnet-bbb
: "${SECURITY_GROUP_IDS:?}" # örn. sg-xxx
: "${TASK_DEFINITION_ARN:?}"
: "${CONTAINER_NAME:=backend}"

OVERRIDES=$(jq -nc \
  --arg name "$CONTAINER_NAME" \
  '{
    containerOverrides: [{
      name: $name,
      command: ["python", "manage.py", "migrate", "--noinput"],
      environment: []
    }]
  }')

aws ecs run-task \
  --region "$AWS_REGION" \
  --cluster "$ECS_CLUSTER" \
  --task-definition "$TASK_DEFINITION_ARN" \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[${SUBNET_IDS}],securityGroups=[${SECURITY_GROUP_IDS}],assignPublicIp=DISABLED}" \
  --overrides "$OVERRIDES" \
  "$@"

echo "Koşumu CloudWatch / ECS konsolundan izleyin; çıkış kodu 0 olana kadar bekleyin."
