"""Record ids (ULID as UUID) and the migrate-check rules."""

import time
import uuid
from pathlib import Path

from app.utils.ids import new_record_id
from scripts.check_migrations import problems

SERVICE_ROOT = Path(__file__).resolve().parents[1]


def test_record_ids_are_uuids() -> None:
    rid = new_record_id()

    assert str(uuid.UUID(rid)) == rid


def test_record_ids_sort_by_creation_time_across_milliseconds() -> None:
    ids: list[str] = []
    for _ in range(5):
        ids.append(new_record_id())
        time.sleep(0.002)

    assert ids == sorted(ids, key=lambda i: uuid.UUID(i).int)


def test_record_ids_do_not_collide() -> None:
    assert len({new_record_id() for _ in range(10_000)}) == 10_000


def _revision(directory: Path, rev: str, down: str | None) -> None:
    (directory / f"{rev}.py").write_text(
        f"revision = {rev!r}\ndown_revision = {down!r}\nbranch_labels = None\n"
        "depends_on = None\n\ndef upgrade():\n    pass\n\ndef downgrade():\n    pass\n"
    )


def _script_dir(tmp_path: Path) -> Path:
    (tmp_path / "versions").mkdir()
    (tmp_path / "env.py").write_text("")
    return tmp_path


def test_the_real_migrations_pass_the_check() -> None:
    assert problems(SERVICE_ROOT / "alembic") == []


def test_two_heads_are_reported(tmp_path: Path) -> None:
    root = _script_dir(tmp_path)
    _revision(root / "versions", "001_a", None)
    _revision(root / "versions", "002_b", "001_a")
    _revision(root / "versions", "002_c", "001_a")

    assert any("exactly one head" in p for p in problems(root))


def test_a_revision_id_over_32_chars_is_reported(tmp_path: Path) -> None:
    root = _script_dir(tmp_path)
    _revision(root / "versions", "001_this_revision_id_is_far_too_long", None)

    assert any("max 32" in p for p in problems(root))
