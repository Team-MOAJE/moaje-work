from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_ENV: str = "development"

    # DB
    DATABASE_URL: str = "mysql+aiomysql://root:root_password@moaje-work-db:3306/work_db"

    # Redis (팀장님 infra container_name)
    REDIS_URL: str = "redis://moaje-redis:6379/0"

    # Kafka (팀장님 infra service name)
    KAFKA_BOOTSTRAP_SERVERS: str = "moaje-kafka:9092"

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

    SECRET_KEY: str = "dev-secret-key-change-in-production"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()