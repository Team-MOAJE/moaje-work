-- ── university 테이블 ────────────────────────────
CREATE TABLE IF NOT EXISTS university (
    id         INT          NOT NULL AUTO_INCREMENT,
    name       VARCHAR(100) NOT NULL,
    short_name VARCHAR(30)  NOT NULL,
    region     VARCHAR(50)  NULL,
    is_active  TINYINT(1)   NOT NULL DEFAULT 1,
    PRIMARY KEY (id),
    UNIQUE KEY uq_university_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── academic_calendar 테이블 ─────────────────────
CREATE TABLE IF NOT EXISTS academic_calendar (
    id                  INT          NOT NULL AUTO_INCREMENT,
    university_id       INT          NOT NULL,
    year                INT          NOT NULL,
    semester            SMALLINT     NOT NULL,
    event_type          ENUM('EXAM','MT','FESTIVAL','VACATION','EMPLOYMENT',
                             'SEMESTER_START','SEMESTER_END',
                             'MIDTERM','FINAL','HOLIDAY') NOT NULL,
    event_name          VARCHAR(100) NOT NULL,
    start_date          DATETIME     NOT NULL,
    end_date            DATETIME     NOT NULL,
    default_extra_spend DECIMAL(18,4) NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    INDEX idx_calendar_univ_semester (university_id, year, semester)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── ai_spending_profile 테이블 ───────────────────
CREATE TABLE IF NOT EXISTS ai_spending_profile (
    id                  BIGINT        NOT NULL AUTO_INCREMENT,
    user_id             BIGINT        NOT NULL,
    university_id       INT           NULL,
    avg_daily_amount    DECIMAL(18,4) NOT NULL DEFAULT 0,
    std_daily_amount    DECIMAL(18,4) NOT NULL DEFAULT 15000,
    tx_count            INT           NOT NULL DEFAULT 0,
    peak_spend_hour     SMALLINT      NOT NULL DEFAULT 0,
    top_category        VARCHAR(50)   NOT NULL DEFAULT '기타',
    risk_score_baseline DECIMAL(5,2)  NOT NULL DEFAULT 0,
    last_analyzed_at    DATETIME      NOT NULL DEFAULT NOW(),
    created_at          DATETIME      NOT NULL DEFAULT NOW(),
    updated_at          DATETIME      NOT NULL DEFAULT NOW() ON UPDATE NOW(),
    PRIMARY KEY (id),
    UNIQUE KEY uq_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── academic_schedule 테이블 ─────────────────────
CREATE TABLE IF NOT EXISTS academic_schedule (
    id                   BIGINT        NOT NULL AUTO_INCREMENT,
    user_id              BIGINT        NOT NULL,
    event_type           ENUM('EXAM','MT','FESTIVAL','VACATION','EMPLOYMENT',
                              'SEMESTER_START','SEMESTER_END',
                              'MIDTERM','FINAL','HOLIDAY') NOT NULL,
    event_name           VARCHAR(100)  NOT NULL,
    start_date           DATETIME      NOT NULL,
    end_date             DATETIME      NOT NULL,
    expected_extra_spend DECIMAL(18,4) NOT NULL DEFAULT 0,
    is_auto              TINYINT(1)    NOT NULL DEFAULT 0,
    created_at           DATETIME      NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id),
    INDEX idx_user_date (user_id, start_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── ai_analysis_log 테이블 ───────────────────────
CREATE TABLE IF NOT EXISTS ai_analysis_log (
    id               BIGINT        NOT NULL AUTO_INCREMENT,
    user_id          BIGINT        NOT NULL,
    analysis_type    ENUM('DAILY_LIMIT','PATTERN_UPDATE','SCHEDULE_ALERT') NOT NULL,
    input_snapshot   JSON          NOT NULL,
    result_message   VARCHAR(500)  NOT NULL,
    daily_limit      DECIMAL(18,4) NULL,
    confidence_score DECIMAL(5,4)  NOT NULL DEFAULT 0,
    created_at       DATETIME      NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── fds_inference_log 테이블 ─────────────────────
CREATE TABLE IF NOT EXISTS fds_inference_log (
    id               BIGINT        NOT NULL AUTO_INCREMENT,
    user_id          BIGINT        NOT NULL,
    transaction_id   VARCHAR(100)  NOT NULL,
    amount           DECIMAL(18,4) NOT NULL,
    merchant         VARCHAR(100)  NULL,
    risk_score       DECIMAL(5,4)  NOT NULL DEFAULT 0,
    risk_level       ENUM('LOW','MEDIUM','HIGH') NOT NULL,
    reason_code      VARCHAR(200)  NULL,
    is_alerted       TINYINT(1)    NOT NULL DEFAULT 0,
    created_at       DATETIME      NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id),
    INDEX idx_fds_user_id (user_id),
    INDEX idx_fds_risk_level (risk_level)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── fds_blacklist 테이블 ─────────────────────────
CREATE TABLE IF NOT EXISTS fds_blacklist (
    id            BIGINT        NOT NULL AUTO_INCREMENT,
    user_id       BIGINT        NOT NULL,
    reason        ENUM('ABNORMAL_AMOUNT','ABNORMAL_TIME','RAPID_REPEAT','MANUAL') NOT NULL,
    description   VARCHAR(300)  NULL,
    is_active     TINYINT(1)    NOT NULL DEFAULT 1,
    registered_at DATETIME      NOT NULL DEFAULT NOW(),
    released_at   DATETIME      NULL,
    PRIMARY KEY (id),
    INDEX idx_blacklist_user_id (user_id),
    INDEX idx_blacklist_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ══════════════════════════════════════════════════
--  대학교 마스터 데이터
-- ══════════════════════════════════════════════════
INSERT IGNORE INTO university (name, short_name, region) VALUES
('한신대학교',   '한신대',   '경기도 오산시'),
('서울대학교',   '서울대',   '서울특별시 관악구'),
('연세대학교',   '연세대',   '서울특별시 서대문구'),
('고려대학교',   '고려대',   '서울특별시 성북구'),
('성균관대학교', '성균관대', '서울특별시 종로구'),
('한양대학교',   '한양대',   '서울특별시 성동구'),
('중앙대학교',   '중앙대',   '서울특별시 동작구'),
('경희대학교',   '경희대',   '서울특별시 동대문구'),
('인하대학교',   '인하대',   '인천광역시 미추홀구'),
('아주대학교',   '아주대',   '경기도 수원시');


-- ══════════════════════════════════════════════════
--  한신대학교 2026년 실제 학사 일정
--  출처: 한신대학교 공식 홈페이지 학사일정
-- ══════════════════════════════════════════════════

-- ── 2026년 1학기 ──────────────────────────────────

-- 개강
INSERT IGNORE INTO academic_calendar
    (university_id, year, semester, event_type, event_name, start_date, end_date, default_extra_spend)
SELECT id, 2026, 1, 'SEMESTER_START', '2026-1학기 개강',
       '2026-03-02 00:00:00', '2026-03-02 23:59:59', 0
FROM university WHERE name = '한신대학교';

-- 중간고사 (4월 21일 ~ 4월 27일)
INSERT IGNORE INTO academic_calendar
    (university_id, year, semester, event_type, event_name, start_date, end_date, default_extra_spend)
SELECT id, 2026, 1, 'MIDTERM', '2026-1학기 중간고사',
       '2026-04-21 00:00:00', '2026-04-27 23:59:59', 45000
FROM university WHERE name = '한신대학교';

-- 기말고사 (6월 9일 ~ 6월 15일)
INSERT IGNORE INTO academic_calendar
    (university_id, year, semester, event_type, event_name, start_date, end_date, default_extra_spend)
SELECT id, 2026, 1, 'FINAL', '2026-1학기 기말고사',
       '2026-06-09 00:00:00', '2026-06-15 23:59:59', 45000
FROM university WHERE name = '한신대학교';

-- 종강 (기말고사 마지막 날)
INSERT IGNORE INTO academic_calendar
    (university_id, year, semester, event_type, event_name, start_date, end_date, default_extra_spend)
SELECT id, 2026, 1, 'SEMESTER_END', '2026-1학기 종강',
       '2026-06-15 00:00:00', '2026-06-15 23:59:59', 0
FROM university WHERE name = '한신대학교';

-- 여름계절학기 (6월 16일 ~ 7월 6일)
INSERT IGNORE INTO academic_calendar
    (university_id, year, semester, event_type, event_name, start_date, end_date, default_extra_spend)
SELECT id, 2026, 1, 'VACATION', '2026 여름방학',
       '2026-06-16 00:00:00', '2026-08-31 23:59:59', 0
FROM university WHERE name = '한신대학교';

-- 개교기념일 (4월 19일)
INSERT IGNORE INTO academic_calendar
    (university_id, year, semester, event_type, event_name, start_date, end_date, default_extra_spend)
SELECT id, 2026, 1, 'HOLIDAY', '개교기념일',
       '2026-04-19 00:00:00', '2026-04-19 23:59:59', 0
FROM university WHERE name = '한신대학교';

-- ── 2026년 2학기 ──────────────────────────────────

-- 개강 (9월 1일)
INSERT IGNORE INTO academic_calendar
    (university_id, year, semester, event_type, event_name, start_date, end_date, default_extra_spend)
SELECT id, 2026, 2, 'SEMESTER_START', '2026-2학기 개강',
       '2026-09-01 00:00:00', '2026-09-01 23:59:59', 0
FROM university WHERE name = '한신대학교';

-- 중간고사 (10월 20일 ~ 10월 26일)
INSERT IGNORE INTO academic_calendar
    (university_id, year, semester, event_type, event_name, start_date, end_date, default_extra_spend)
SELECT id, 2026, 2, 'MIDTERM', '2026-2학기 중간고사',
       '2026-10-20 00:00:00', '2026-10-26 23:59:59', 45000
FROM university WHERE name = '한신대학교';

-- 기말고사 (12월 8일 ~ 12월 14일)
INSERT IGNORE INTO academic_calendar
    (university_id, year, semester, event_type, event_name, start_date, end_date, default_extra_spend)
SELECT id, 2026, 2, 'FINAL', '2026-2학기 기말고사',
       '2026-12-08 00:00:00', '2026-12-14 23:59:59', 45000
FROM university WHERE name = '한신대학교';

-- 종강 (기말고사 마지막 날)
INSERT IGNORE INTO academic_calendar
    (university_id, year, semester, event_type, event_name, start_date, end_date, default_extra_spend)
SELECT id, 2026, 2, 'SEMESTER_END', '2026-2학기 종강',
       '2026-12-14 00:00:00', '2026-12-14 23:59:59', 0
FROM university WHERE name = '한신대학교';

-- 겨울방학 (12월 15일 ~)
INSERT IGNORE INTO academic_calendar
    (university_id, year, semester, event_type, event_name, start_date, end_date, default_extra_spend)
SELECT id, 2026, 2, 'VACATION', '2026 겨울방학',
       '2026-12-15 00:00:00', '2027-02-28 23:59:59', 0
FROM university WHERE name = '한신대학교';

-- 추석연휴 (9월 24일 ~ 9월 26일)
INSERT IGNORE INTO academic_calendar
    (university_id, year, semester, event_type, event_name, start_date, end_date, default_extra_spend)
SELECT id, 2026, 2, 'HOLIDAY', '추석연휴',
       '2026-09-24 00:00:00', '2026-09-26 23:59:59', 0
FROM university WHERE name = '한신대학교';
