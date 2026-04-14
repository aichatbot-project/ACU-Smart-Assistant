# Deployment dosyaları

| Dosya | Amaç |
|--------|------|
| `env.production.example` | `.env.production` ve AWS task env için başlıca değişken listesi (kök dizine kopyalayın). |
| `github-oidc-trust-policy.json` | GitHub Actions OIDC için IAM role **trust policy** taslağı (`YOUR_*` alanlarını değiştirin). |
| `iam-ecr-ecs-deploy-policy.json` | CD rolüne eklenecek **inline policy** taslağı (ECR + ECS; repoları ve account’u özelleştirin). |
| `ecs-migrate-run-task.example.sh` | Fargate üzerinde tek seferlik `migrate` için `aws ecs run-task` örneği (`jq` gerekir). |

Kök dizinde:

- `docker-compose.prod.yml` — sunucuda kaynak koddan `--build` ile üretim imajları (pgvector + Ollama + Nginx).
- `docker-compose.ec2.yml` — **aynı stack, backend/frontend sadece ECR imajı**; GitHub Actions `main` push sonrası SSH ile bunu kullanır. EC2’de repoda bu dosya ve `.env.production` olmalı; instance role veya kullanıcıya **ECR okuma** yetkisi gerekir.
- `DEPLOYMENT.md` — genel kontrol listesi ve sıra.

Backend konteynerinde tek replikada migrate için ortam değişkeni: `RUN_MIGRATIONS=1` (entrypoint `docker-entrypoint.sh`). Çoklu replikada bunu aynı anda açmayın; bunun yerine `ecs-migrate-run-task.example.sh` benzeri tek görev kullanın.

**docker-compose.prod.yml:** `frontend` build arg `NEXT_PUBLIC_API_URL` Compose tarafından **proje kökündeki `.env`** dosyasından veya kabuktan okunur; sadece `.env.production` yazmak yetmez (Compose bu dosyayı interpolasyon için yüklemez). Kök `.env` içine aynı satırı ekleyin veya `NEXT_PUBLIC_API_URL=https://... docker compose -f docker-compose.prod.yml up --build`.
