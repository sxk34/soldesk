# Ticketing System — AWS 배포 뼈대

영화/콘서트 예매 시스템의 AWS 배포용 프로젝트 스켈레톤.
원본(thesky3303/awstest)의 코드를 기반으로, 로컬 Redis → ElastiCache, SQS 시뮬레이션 → 실제 AWS SQS FIFO로 전환.

## 아키텍처

```
[사용자] → ALB Ingress
              ├── /api/read/*  → read-api (Port 5000)  ← DB(RDS Reader) + Cache(ElastiCache)
              ├── /api/write/* → write-api (Port 5001) ← DB(RDS Writer) + SQS FIFO
              └── /health      → read-api
                                    ↓
                              SQS FIFO Queue
                                    ↓
                              worker-svc  → DB(RDS Writer) + Redis(결과 저장)
```

- **read-api / write-api**: 동일 Docker 이미지(`ticketing-was`), CMD 다르게 실행
- **worker-svc**: SQS 메시지 폴링 → 예매 트랜잭션 처리 → 결과를 Redis에 저장
- **듀얼 모드**: `SQS_QUEUE_URL` 비어있으면 원본 동기 로직으로 폴백 (로컬 개발 가능)

## 프로젝트 구조

```
ticketing-skeleton/
├── terraform/              # AWS 인프라 (VPC, EKS, RDS, ElastiCache, SQS)
│   ├── main.tf
│   ├── variables.tf
│   ├── outputs.tf
│   └── modules/
│       ├── network/        # VPC, 서브넷, 보안그룹
│       ├── eks/            # EKS 클러스터, ALB 컨트롤러, IRSA
│       ├── rds/            # MySQL 8.0 (Writer + Reader)
│       ├── elasticache/    # Redis 7.0 Replication Group
│       └── sqs/            # FIFO 큐 + DLQ
├── services/
│   ├── ticketing-was/      # FastAPI 백엔드 (read-api + write-api)
│   │   ├── config.py       # 환경변수 기반 설정
│   │   ├── db.py           # DB 연결
│   │   ├── sqs_client.py   # SQS FIFO 클라이언트
│   │   ├── read_app.py     # 읽기 전용 API (포트 5000)
│   │   ├── write_app.py    # 쓰기 API (포트 5001)
│   │   ├── cache/          # Redis 캐시 (ElastiCache)
│   │   ├── theater/        # 극장/영화 예매
│   │   ├── concert/        # 콘서트 예매
│   │   ├── movie/          # 영화 정보 캐시
│   │   ├── user/           # 사용자 관리, 환불
│   │   ├── auth/           # 회원가입, 로그인, 비밀번호
│   │   ├── review/         # 리뷰
│   │   └── inquiry/        # 문의
│   └── worker-svc/         # SQS 소비자 (예매 처리 워커)
├── k8s/                    # Kubernetes 매니페스트
│   ├── namespace.yaml
│   ├── configmap.yaml
│   ├── sqs-service-account.yaml   # IRSA
│   ├── read-api/
│   ├── write-api/
│   ├── worker-svc/
│   ├── ingress.yaml        # ALB Ingress
│   └── kustomization.yaml
├── frontend/               # 정적 프론트엔드 (src/ 에 복사)
├── db-schema/              # DB 스키마 (thesky3303 원본 참조)
└── .github/workflows/      # CI/CD (ECR + EKS 롤링 배포)
```

## 배포 가이드

### 1단계: Terraform으로 인프라 생성

```bash
cd terraform

# terraform.tfvars 작성
cat > terraform.tfvars <<EOF
env              = "prod"
aws_region       = "ap-northeast-2"
db_password      = "안전한비밀번호"
eks_cluster_name = "ticketing-eks"
github_repo      = "your-org/ticketing"
EOF

terraform init
terraform plan
terraform apply
```

생성되는 리소스:
| 리소스 | 설명 |
|--------|------|
| VPC | 10.0.0.0/16, 퍼블릭 2 + 프라이빗 2 서브넷 |
| EKS | t3.small x2 노드, ALB Controller, Cluster Autoscaler |
| RDS | MySQL 8.0 db.t3.micro (Writer + Reader Replica) |
| ElastiCache | Redis 7.0 cache.t3.micro (2 노드, 자동 장애조치) |
| SQS | FIFO 큐 + DLQ (5회 실패 시 전환) |

### 2단계: Docker 이미지 빌드 & ECR 푸시

```bash
# ECR 레포 생성 (최초 1회)
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=ap-northeast-2

aws ecr create-repository --repository-name ticketing/ticketing-was --region $REGION
aws ecr create-repository --repository-name ticketing/worker-svc --region $REGION

# ECR 로그인
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com

# ticketing-was 빌드 & 푸시
cd services/ticketing-was
docker build -t $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/ticketing/ticketing-was:latest .
docker push $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/ticketing/ticketing-was:latest

# worker-svc 빌드 & 푸시
cd ../worker-svc
docker build -t $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/ticketing/worker-svc:latest .
docker push $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/ticketing/worker-svc:latest
```

### 3단계: DB 스키마 초기화

```bash
# Terraform 출력에서 RDS 엔드포인트 확인
terraform -chdir=terraform output rds_writer_endpoint

# DB 접속 후 thesky3303 원본의 SQL 스키마 실행
mysql -h <RDS_WRITER_ENDPOINT> -u admin -p ticketing_test < db-schema/init.sql
```

> **참고**: DB 스키마 SQL은 thesky3303/awstest 레포의 `3 tier/ticketing-db/` 또는 초기설정 폴더에서 가져와야 합니다.

### 4단계: K8s Secret 생성 & 매니페스트 배포

```bash
# EKS kubeconfig
aws eks update-kubeconfig --name ticketing-eks --region ap-northeast-2

# Terraform output에서 값 가져오기
DB_WRITER=$(terraform -chdir=terraform output -raw rds_writer_endpoint)
DB_READER=$(terraform -chdir=terraform output -raw rds_reader_endpoint)
REDIS_EP=$(terraform -chdir=terraform output -raw redis_endpoint)
SQS_URL=$(terraform -chdir=terraform output -raw sqs_queue_url)
SQS_ROLE=$(terraform -chdir=terraform output -raw sqs_access_role_arn)
ACCOUNT_ID=$(terraform -chdir=terraform output -raw aws_account_id)

# namespace 먼저
kubectl apply -f k8s/namespace.yaml

# Secret 생성 (민감 정보)
kubectl create secret generic ticketing-secrets -n ticketing \
  --from-literal=DB_WRITER_HOST="$DB_WRITER" \
  --from-literal=DB_READER_HOST="$DB_READER" \
  --from-literal=DB_USER=admin \
  --from-literal=DB_PASSWORD="DB비밀번호" \
  --from-literal=REDIS_HOST="$REDIS_EP" \
  --from-literal=SQS_QUEUE_URL="$SQS_URL"

# kustomization 이미지 경로 수정
cd k8s
kustomize edit set image \
  ticketing/ticketing-was=$ACCOUNT_ID.dkr.ecr.ap-northeast-2.amazonaws.com/ticketing/ticketing-was:latest \
  ticketing/worker-svc=$ACCOUNT_ID.dkr.ecr.ap-northeast-2.amazonaws.com/ticketing/worker-svc:latest

# SQS Service Account의 annotation 수정
sed -i "s|ACCOUNT_ID|$ACCOUNT_ID|g" sqs-service-account.yaml

# 전체 배포
kubectl apply -k .
```

### 5단계: 확인

```bash
# Pod 상태 확인
kubectl get pods -n ticketing

# ALB 주소 확인
kubectl get ingress -n ticketing

# 헬스체크
curl http://<ALB_ADDRESS>/health
curl http://<ALB_ADDRESS>/api/read/health
curl http://<ALB_ADDRESS>/api/write/health
```

## 프론트엔드 배포

정적 파일(HTML/CSS/JS)은 `frontend/src/`에 위치합니다.

```bash
# thesky3303/awstest에서 프론트엔드 복사
cp -r "3 tier/ticketing-web/"* frontend/src/
```

배포 방법 (택 1):
- **방법 A**: Nginx 컨테이너에 넣어 EKS에서 서빙 (k8s/frontend/ 추가 필요)
- **방법 B**: S3 + CloudFront로 정적 호스팅

---

## 사람이 직접 해야 할 것

### DB 스키마

- `db-schema/` 폴더에 thesky3303 원본의 init SQL을 넣어야 합니다
- 테이블: `users`, `movies`, `theaters`, `halls`, `hall_seats`, `schedules`, `booking`, `booking_seats`, `payment`, `concerts`, `concert_shows`, `concert_booking`, `concert_booking_seats`, `concert_payment`, `reviews`, `inquiries`

### CI/CD (선택)

- `.github/workflows/deploy.yml`에 GitHub Actions 설정이 포함되어 있음
- 필요한 GitHub Secrets: `AWS_ACCOUNT_ID`, `AWS_ROLE_ARN`, `EKS_CLUSTER`

### 비용 참고

> 테스트 후 반드시 `terraform destroy`로 리소스를 정리하세요.

## 로컬 개발

SQS/ElastiCache 없이 로컬에서 실행 가능 (듀얼 모드):

```bash
cd services/ticketing-was

# 환경변수 (로컬 MySQL + Redis)
export DB_WRITER_HOST=127.0.0.1
export DB_PASSWORD=your_password
export REDIS_HOST=127.0.0.1
export SQS_QUEUE_URL=""          # 비워두면 동기 모드

# read-api
uvicorn read_app:app --port 5000

# write-api (별도 터미널)
uvicorn write_app:app --port 5001
```

`SQS_QUEUE_URL`이 빈 문자열이면 예매 요청이 SQS를 거치지 않고 원본 동기 로직으로 직접 처리됩니다.
