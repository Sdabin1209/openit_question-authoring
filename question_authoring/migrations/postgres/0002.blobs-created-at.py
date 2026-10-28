"""원본 자료에 보존 기간 기준 시각(created_at) 열 추가(D88 U4). 이미 있는 DB는 건너뛴다."""
from yoyo import step

__depends__ = {"0001.initial"}

step("ALTER TABLE blobs ADD COLUMN IF NOT EXISTS created_at TEXT")
