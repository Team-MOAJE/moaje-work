"""
시각 기준을 한곳에 모아둔다.

■ 저장은 UTC, 날짜 경계는 한국시간

DB 의 타임스탬프는 전부 server_default=func.now() 로 들어간다. 값을 만드는
주체는 MySQL 이고 컨테이너는 UTC 다. 즉 저장된 값은 UTC 다.

그런데 사용자에게 '며칠'은 한국 날짜다. 한국시간 새벽 1시에 쓴 돈은
UTC 로는 전날 16시라서, UTC 날짜로 묶으면 어제 쓴 것으로 잡힌다.
학기 리포트의 '기록 일수', Recap 의 '그 달 거래 건수', '7일 이내 이벤트'가
모두 이 경계에 걸린다.

그래서 저장은 UTC 로 두고, 날짜·월 경계만 한국시간으로 계산한다.
MySQL 의 TZ 를 Asia/Seoul 로 바꾸는 방법은 쓰지 않는다. 그러면 새로
들어오는 값만 KST 가 되고 이미 쌓인 UTC 행은 9시간 밀려 읽힌다.
쓰던 DB 를 깨뜨리는 변경은 하지 않는다는 원칙(schema_sync 참고)과 같다.

■ '기간'과 '날짜'는 다르다

  - 최근 30일, 10분 안에 3건처럼 길이로 재는 것   → utc_naive_now()
    시간대와 무관하다. 어느 기준으로 재도 길이는 같다.
  - 며칠·몇 월로 묶는 것                          → kst_* 함수
    여기만 한국 날짜가 필요하다.

■ 비교할 때 시간대를 붙이지 않는다

칸이 시간대 없는 DATETIME 이므로, 비교·저장에 넘기는 값도 시간대를 떼서
보낸다. 붙은 값을 그대로 넘기면 드라이버가 '+00:00' 이 붙은 문자열을 보내고
MySQL 버전·세션 설정에 따라 조용히 어긋난다. Kafka consumer 에서도 같은
이유로 tzinfo 를 벗겼다.

한국은 일광절약시간이 없어 KST 는 항상 UTC+9 다. 그래서 9시간 고정 이동으로
충분하고, 시간대 데이터베이스(CONVERT_TZ 용 tz 테이블)가 필요 없다.
compose 가 MYSQL_INITDB_SKIP_TZINFO=1 로 띄우므로 그 테이블은 없다.
"""
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, text

KST_OFFSET_HOURS = 9
KST = timezone(timedelta(hours=KST_OFFSET_HOURS))
_SHIFT = timedelta(hours=KST_OFFSET_HOURS)


# ── 길이로 재는 것 (시간대 무관) ───────────────────

def utc_naive_now() -> datetime:
    """DB 의 DATETIME 칸과 비교·저장할 수 있는 '지금'. 최근 N일·N분에 쓴다."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── 날짜로 묶는 것 (한국 날짜) ─────────────────────

def kst_now() -> datetime:
    """한국시간 벽시계. 며칠·몇 시인지 판단할 때 쓴다."""
    return datetime.now(KST).replace(tzinfo=None)


def kst_today() -> date:
    """한국 날짜 기준 오늘."""
    return kst_now().date()


def kst_day_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """
    한국 날짜 [start, end] 을 UTC 범위로 바꾼다.
    '시작일 0시(KST) 이상, 종료일 다음날 0시(KST) 미만'.

    칸에 함수를 씌우지 않고 범위 비교만 남기므로 인덱스를 그대로 탄다.
    """
    lo = datetime.combine(start, time.min) - _SHIFT
    hi = datetime.combine(end + timedelta(days=1), time.min) - _SHIFT
    return lo, hi


def kst_month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    """한국 날짜 기준 그 달 전체를 UTC 범위로 바꾼다."""
    first = date(year, month, 1)
    nxt   = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return (
        datetime.combine(first, time.min) - _SHIFT,
        datetime.combine(nxt,   time.min) - _SHIFT,
    )


def kst_date(column):
    """
    UTC 로 저장된 칸에서 '한국 날짜'를 뽑는 SQL 식. GROUP BY 에 쓴다.

    범위를 좁힐 때는 kst_day_bounds 로 경계를 옮기고, 묶을 때만 이걸 쓴다.
    묶는 쪽은 이미 좁혀진 결과 위에서 도니까 인덱스에 영향이 없다.
    """
    return func.date(func.date_add(column, text(f"INTERVAL {KST_OFFSET_HOURS} HOUR")))
