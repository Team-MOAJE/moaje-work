"""
이미 만들어진 표에 빠진 칸·인덱스를 채우는 보정 단계.

Base.metadata.create_all 은 '없는 표'만 만든다. 이미 있는 표는 손대지 않으므로
모델에 칸이나 인덱스를 새로 넣어도 기존 DB 에는 아무 일도 일어나지 않는다.
비어 있는 DB 로 띄우면 멀쩡히 돌고, 쓰던 DB 로 띄우면
Unknown column 으로 죽는다. 데이터가 쌓인 뒤에야 드러나는 종류의 사고다.

그래서 시작할 때 모델과 실제 DB 를 맞춰본다. 규칙은 하나다.

  **더하기만 한다.**

칸 추가와 인덱스 추가만 하고, 칸을 지우거나 자료형을 바꾸는 일은 절대 하지 않는다.
사람이 봐야 하는 변경은 경고만 남기고 넘어간다 (아래 참고). 이렇게 두면
몇 번 실행해도 결과가 같고, 데이터를 잃을 수 있는 수정은 사람 손을 거친다.

NOT NULL 인데 기본값이 없는 칸은 추가하지 않는다. 줄이 이미 있는 표에
그런 칸을 붙이면 DB 가 거절하거나, 더 나쁘게는 0·빈 문자열을 조용히 채운다.
이때는 경고를 남겨 사람이 직접 판단하게 한다.
"""
import logging

from sqlalchemy import inspect, text
from sqlalchemy.schema import CreateIndex

from app.db.session import Base

logger = logging.getLogger(__name__)


def _missing_columns(inspector, table) -> list:
    have = {c["name"] for c in inspector.get_columns(table.name)}
    return [c for c in table.columns if c.name not in have]


def _missing_indexes(inspector, table) -> list:
    have = {i["name"] for i in inspector.get_indexes(table.name)}
    # 유니크 제약은 인덱스 목록에 안 잡히는 DB 도 있어 따로 모은다.
    try:
        have |= {u["name"] for u in inspector.get_unique_constraints(table.name)}
    except NotImplementedError:
        pass
    return [idx for idx in table.indexes if idx.name not in have]


def _column_spec(dialect, column) -> str | None:
    """ADD COLUMN 에 쓸 '이름 자료형 NULL여부 기본값' 조각을 만든다."""
    try:
        compiler = dialect.ddl_compiler(dialect, None)
        return compiler.get_column_specification(column)
    except Exception as e:
        logger.warning(f"⚠️ 칸 정의를 못 만들었습니다 | {column.name} | {e}")
        return None


def _sync(connection) -> None:
    inspector = inspect(connection)
    existing  = set(inspector.get_table_names())
    dialect   = connection.dialect

    added_cols = added_idx = 0

    for table in Base.metadata.sorted_tables:
        if table.name not in existing:
            continue   # create_all 이 방금 만든 표 — 이미 모델과 같다

        have_cols = {c["name"] for c in inspector.get_columns(table.name)}

        for column in _missing_columns(inspector, table):
            # 파이썬 쪽 default 는 이미 들어 있는 줄을 채워주지 못한다.
            # 기존 줄에 값을 넣어줄 수 있는 건 server_default 뿐이다.
            if not column.nullable and column.server_default is None:
                logger.warning(
                    f"⚠️ 손으로 처리해야 하는 칸 | {table.name}.{column.name} "
                    f"| NOT NULL 인데 기본값이 없어 자동으로 추가하지 않습니다. "
                    f"기본값을 정해 직접 ALTER TABLE 해주세요."
                )
                continue

            spec = _column_spec(dialect, column)
            if spec is None:
                continue
            try:
                connection.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {spec}"))
                added_cols += 1
                have_cols.add(column.name)
                logger.info(f"🔧 칸 추가 | {table.name}.{column.name}")
            except Exception as e:
                logger.warning(f"⚠️ 칸 추가 실패 | {table.name}.{column.name} | {e}")

        for index in _missing_indexes(inspector, table):
            # 방금 추가에 실패한 칸을 쓰는 인덱스는 당연히 못 만든다.
            missing = [c.name for c in index.columns if c.name not in have_cols]
            if missing:
                logger.warning(
                    f"⚠️ 인덱스 건너뜀 | {index.name} | 칸이 없습니다: {', '.join(missing)}"
                )
                continue
            try:
                connection.execute(CreateIndex(index))
                added_idx += 1
                logger.info(f"🔧 인덱스 추가 | {index.name}")
            except Exception as e:
                logger.warning(f"⚠️ 인덱스 추가 실패 | {index.name} | {e}")

    if added_cols or added_idx:
        logger.info(f"✅ 스키마 보정 완료 | 칸 {added_cols}개 · 인덱스 {added_idx}개 추가")
    else:
        logger.info("✅ 스키마 보정 | 추가할 것 없음")


async def sync_schema(engine) -> None:
    """
    모델에 있고 DB 에 없는 칸·인덱스를 채운다.

    보정이 실패해도 서비스는 떠야 한다. 여기서 막아 세우면
    인덱스 하나 때문에 서비스 전체가 못 뜨는 쪽이 더 나쁘다.
    대신 경고를 남겨 사람이 보게 한다.
    """
    try:
        async with engine.begin() as conn:
            await conn.run_sync(_sync)
    except Exception as e:
        logger.warning(f"⚠️ 스키마 보정 건너뜀 (서비스는 계속 시작) | {e}")
