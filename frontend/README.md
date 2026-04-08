# 프론트엔드

형(thesky3303)의 프론트엔드 파일을 `src/` 폴더에 복사하세요:

```bash
# awstest 레포 클론 후:
cp -r "3 tier/ticketing-web/"* frontend/src/
```

복사 후 구조:
```
frontend/src/
  index.html
  css/
  js/
    common/
    concert/
    main/
    movie/
    theaters/
    user/
  images/
```

프론트엔드는 정적 파일이므로 EKS에서 Nginx 컨테이너로 서빙하거나,
S3 + CloudFront를 추가하여 배포할 수 있습니다.

API 호출 경로:
- Read API: `/api/read/*` (read-api.js)
- Write API: `/api/write/*` (write-api.js)
- ALB Ingress가 이 경로를 각 서비스로 라우팅합니다.
