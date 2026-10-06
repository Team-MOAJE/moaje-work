"""
저장된 시각과 비교할 때 쓰는 '지금'.

DB 의 타임스탬프는 전부 server_default=func.now() 로 들어간다. 즉 값을 만드는
주체는 MySQL 이고, 칸은 시간대 정보가 없는 DATETIME 이다.

여기에 파이썬에서 datetime.now(timezone.utc) 를 그대로 넣거나 비교하면
시간대가 붙은 값과 안 붙은 값을 섞는 셈이 된다. 드라이버가 '+00:00' 이
붙은 문자열을 보내고, MySQL 버전·세션 설정에 따라 조용히 어긋나거나
변환에 실패한다. Kafka consumer 에서도 같은 이유로 tzinfo 를 벗겼다.

그래서 저장·비교용 '지금'은 전부 이 함수로 구한다. UTC 로 계산한 뒤
시간대 표시만 떼어내 칸의 모양과 맞춘다.

※ 컨테이너와 MySQL 이 둘 다 UTC 라는 전제다. 어느 한쪽에 TZ 를 다르게
   주면 '며칠에 일어난 일인지'가 어긋난다. 앱 입장에서 하루 경계를
   한국 시간으로 볼지는 따로 정해야 할 문제다.
"""
from datetime import date, datetime, timezone


def utc_naive_now() -> datetime:
    """DB 의 DATETIME 칸과 비교·저장할 수 있는 '지금'."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utc_today() -> date:
    """DB 에 저장된 시각과 같은 기준의 '오늘'."""
    return utc_naive_now().date()
