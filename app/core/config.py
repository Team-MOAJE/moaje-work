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
    KAFKA_CONSUMER_GROUP: str = "moaje-work-group"

    # Kafka 토픽 - Producer (Work → 외부)
    KAFKA_TOPIC_SPENDING_ANALYZED: str = "work.spending.analyzed"
    KAFKA_TOPIC_SCHEDULE_UPDATED:  str = "work.schedule.updated"
    KAFKA_TOPIC_FDS_ALERT:         str = "work.fds.alert"

    # Kafka 토픽 - Consumer (외부 → Work)
    KAFKA_TOPIC_BALANCE_DEDUCTED:     str = "asset.balance.deducted"
    KAFKA_TOPIC_DAILY_BUDGET_UPDATED: str = "asset.daily.budget.updated"

    # Kafka 토픽 - Consumer (Auth → Work)
    KAFKA_TOPIC_USER_REGISTERED:  str = "auth.user.registered"   # 신규 유저 소비 프로필 자동 생성
    KAFKA_TOPIC_USER_LOGGED_IN:   str = "auth.user.logged_in"    # 활동 로그 (선택)

    # Kafka 토픽 - Consumer (Banking → Work)
    # ✅ 팀장님 요청: Banking 거래 완료 이벤트 → FDS 자동 분석
    # 토픽명은 팀장님과 확정 후 변경 예정
    KAFKA_TOPIC_BANKING_TRANSACTION: str = "banking.transaction.created"

    SECRET_KEY: str = "dev-secret-key-change-in-production"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()