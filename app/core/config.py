from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_ENV: str = "development"

    # DB
    DATABASE_URL: str = "mysql+aiomysql://root:root_password@moaje-work-db:3306/work_db"

    # Redis (팀장님 infra container_name)
    REDIS_URL: str = "redis://moaje-redis:6379/0"

    # 공용 Redis 키 접두어
    # moaje-redis 는 Auth·Asset 등과 공유하는 인스턴스이므로
    # 도메인 접두어 없이 daily_limit:{id} 같은 키를 쓰면 충돌 위험이 있다.
    # 모든 Work 캐시 키는 이 접두어를 붙인다. (삭제 책임: Work)
    REDIS_KEY_PREFIX: str = "work:"

    # Kafka (팀장님 infra service name)
    KAFKA_BOOTSTRAP_SERVERS: str = "kafka:29092"

    # Kafka Consumer Group
    # 같은 이벤트를 여러 도메인이 각각 받아야 하므로 도메인별로 그룹을 분리한다.
    KAFKA_CONSUMER_GROUP: str = "moaje-work"

    # Kafka 토픽 - Producer (Work → 외부)
    KAFKA_TOPIC_SPENDING_ANALYZED: str = "work.spending.analyzed"
    KAFKA_TOPIC_SCHEDULE_UPDATED:  str = "work.schedule.updated"
    KAFKA_TOPIC_FDS_ALERT:         str = "work.fds.alert"

    # ── Kafka 토픽 - Consumer (Asset → Work) ─────────────────────
    # 2026-09-15 회의 확정 계약 (kafka-topics.md 기준)
    # Banking 은 Asset 에만 발행하므로 Work 는 Asset 을 통해 거래를 수신한다.

    # 거래 성공 원시 데이터 (Protobuf) — FDS 자동 분석 트리거
    KAFKA_TOPIC_TRANSACTION_SUCCEEDED: str = "transaction_succeeded_events"

    # 사용자·월별 수입/지출/자금이동 집계 (Money Recap 생성용)
    KAFKA_TOPIC_MONTHLY_CASHFLOW: str = "moaje.asset.monthly-cashflow-aggregated"

    # 사용자·월별 카테고리 집계 (소비패턴 별명 생성용)
    KAFKA_TOPIC_CATEGORY_CASHFLOW: str = "moaje.asset.category-cashflow-aggregated"

    # ── Kafka 토픽 - Consumer (Auth → Work) ──────────────────────
    # ⚠️ Auth 의 Kafka 사용 여부 미확정 (kafka-topics.md)
    #    발행되지 않아도 첫 API 호출 시 프로필을 생성하는 fallback 이 있어 무방하다.
    KAFKA_TOPIC_USER_REGISTERED:  str = "auth.user.registered"

    # ── gRPC ────────────────────────────────────────────────────
    # Work 가 호출하는 상대 서비스 주소.
    # 연결 실패 시 서비스를 중단하지 않고 기본값으로 계산을 이어간다.
    ASSET_GRPC_TARGET : str = "moaje-asset:9090"
    GRPC_TIMEOUT_SEC  : float = 3.0

    SECRET_KEY: str = "dev-secret-key-change-in-production"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()