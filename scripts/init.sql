CREATE DATABASE IF NOT EXISTS work_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE work_db;

CREATE TABLE IF NOT EXISTS ai_spending_profile (
    id                  BIGINT          NOT NULL AUTO_INCREMENT,
    user_id             BIGINT          NOT NULL,
    avg_daily_amount    DECIMAL(18,4)   NOT NULL DEFAULT 0,
    peak_spend_hour     SMALLINT        NOT NULL DEFAULT 0,
    top_category        VARCHAR(50)     NOT NULL,
    risk_score_baseline DECIMAL(5,2)    NOT NULL DEFAULT 0,
    last_analyzed_at    DATETIME        NOT NULL DEFAULT NOW(),
    created_at          DATETIME        NOT NULL DEFAULT NOW(),
    updated_at          DATETIME        NOT NULL DEFAULT NOW() ON UPDATE NOW(),
    PRIMARY KEY (id),
    UNIQUE KEY uq_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS academic_schedule (
    id                   BIGINT        NOT NULL AUTO_INCREMENT,
    user_id              BIGINT        NOT NULL,
    event_type           ENUM('EXAM','MT','FESTIVAL','VACATION','EMPLOYMENT') NOT NULL,
    event_name           VARCHAR(100)  NOT NULL,
    start_date           DATE          NOT NULL,
    end_date             DATE          NOT NULL,
    expected_extra_spend DECIMAL(18,4) NOT NULL DEFAULT 0,
    created_at           DATETIME      NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id),
    INDEX idx_user_date (user_id, start_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ai_analysis_log (
    id               BIGINT        NOT NULL AUTO_INCREMENT,
    user_id          BIGINT        NOT NULL,
    analysis_type    ENUM('DAILY_LIMIT','PATTERN_UPDATE','SCHEDULE_ALERT') NOT NULL,
    input_snapshot   JSON          NOT NULL,
    result_message   VARCHAR(500)  NOT NULL,
    daily_limit      DECIMAL(18,4) NULL,
    confidence_score DECIMAL(5,4)  NOT NULL DEFAULT 0,
    created_at       DATETIME      NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id),
    INDEX idx_user_type (user_id, analysis_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 샘플 데이터
INSERT INTO ai_spending_profile (user_id, avg_daily_amount, peak_spend_hour, top_category, risk_score_baseline)
VALUES (1001, 35000.0000, 12, '식비', 0.15),
       (1002, 52000.0000, 19, '쇼핑', 0.22);

INSERT INTO academic_schedule (user_id, event_type, event_name, start_date, end_date, expected_extra_spend)
VALUES (1001, 'EXAM', '2025-1 기말고사', '2025-06-16', '2025-06-20', 45000.0000),
       (1001, 'MT',   '과 MT',           '2025-05-02', '2025-05-04', 80000.0000);
