# DB 스키마

형(thesky3303)의 원본 SQL 파일을 여기에 복사하세요:

```bash
# awstest 레포 클론 후:
cp "3 tier/ticketing-db/db 생성용.sql"        db-schema/01-init.sql
cp "3 tier/ticketing-db/concert_schema.sql"    db-schema/02-concert.sql
cp "3 tier/ticketing-db/concert_seed.sql"      db-schema/03-concert-seed.sql
cp "3 tier/ticketing-db/생성,추가용.sql"       db-schema/04-seed.sql
```

RDS에 적용:
```bash
mysql -h <rds-writer-endpoint> -u root -p < db-schema/01-init.sql
mysql -h <rds-writer-endpoint> -u root -p < db-schema/02-concert.sql
# 시드 데이터도 필요하면 추가
```
