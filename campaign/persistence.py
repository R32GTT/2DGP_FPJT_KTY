"""JSON 저장·복원. 로드 자체는 시간·난수·작전을 갱신하지 않는다."""

from dataclasses import asdict, fields
import json
import os
from pathlib import Path
import tempfile

from .models import (CampaignState, Event, ExternalDemand, ExternalSource, Facility,
                     Ledger, Node, Operation, Position, Region, Reservation, Road, Unit, Vehicle)
from .resources import assert_invariants


SAVE_VERSION = 3
SAVE_FORMAT = "revenant_lost.campaign"


class CampaignSaveError(ValueError):
    """저장 파일 부재·손상·버전 불일치를 호출자에게 전달한다."""


def serialize_campaign(state: CampaignState) -> str:
    assert_invariants(state)
    return json.dumps({"format": SAVE_FORMAT, "save_version": SAVE_VERSION,
                       "state": asdict(state)}, ensure_ascii=False, allow_nan=False, indent=2)


def _construct(cls, row):
    if not isinstance(row, dict) or set(row) != {field.name for field in fields(cls)}:
        raise CampaignSaveError(f"저장 필드 불일치: {cls.__name__}")
    return cls(**row)


def _tuples(value):
    return tuple(_tuples(item) for item in value) if isinstance(value, list) else value


def deserialize_campaign(text: str) -> CampaignState:
    try:
        payload = json.loads(text)
        if (payload["format"] != SAVE_FORMAT or type(payload["save_version"]) is not int
                or payload["save_version"] != SAVE_VERSION):
            raise CampaignSaveError("지원하지 않는 캠페인 저장 형식·버전")
        row = dict(payload["state"])
        row.setdefault("repair_requests", {})
        for key, cls in (("roads", Road), ("reservations", Reservation)):
            row[key] = {key: _construct(cls, value) for key, value in row[key].items()}
        row["facilities"] = {
            key: _construct(Facility, {"relay_disabled": False, "commander_lost": False,
                                       "watch_disabled": False, **value})
            for key, value in row["facilities"].items()
        }
        row["regions"] = {key: _construct(Region, {"weak_since": None, "withdrawal_started_at": None, **value})
                          for key, value in row["regions"].items()}
        # 좌표가 추가되기 전에 저장한 작전은 별도 목격 위치 없이 복원한다.
        row["operations"] = {key: _construct(Operation, {"report_xy": None, "operating_distance_m": 0.0, **value})
                             for key, value in row["operations"].items()}
        row["nodes"] = {key: _construct(Node, {**value, "position": _construct(Position, value["position"])})
                        for key, value in row["nodes"].items()}
        for key, cls in (("units", Unit), ("vehicles", Vehicle)):
            defaults = {"can_escort": True} if cls is Unit else {}
            row[key] = {key: _construct(cls, {**defaults, **value, "position":
                        _construct(Position, value["position"]) if value["position"] is not None else None})
                        for key, value in row[key].items()}
        row["source"] = _construct(ExternalSource, row["source"])
        row["demand"] = _construct(ExternalDemand, row["demand"])
        row["ledger"] = _construct(Ledger, row["ledger"])
        row["events"] = [_construct(Event, event) for event in row["events"]]
        row["rng_state"] = _tuples(row["rng_state"])
        state = _construct(CampaignState, row)
        assert_invariants(state)
        return state
    except CampaignSaveError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, IndexError, OverflowError) as exc:
        raise CampaignSaveError(f"손상된 캠페인 저장 데이터: {exc}") from exc


def save_campaign(state: CampaignState, path) -> None:
    write_save(serialize_campaign(state).encode("utf-8"), path)


def write_save(data: bytes, path) -> None:
    """완성된 저장 데이터만 원자적으로 교체한다."""
    destination = Path(path)
    temporary = None
    try:
        # 완성된 임시 파일만 교체해 저장 도중 실패가 기존 세이브를 훼손하지 않게 한다.
        with tempfile.NamedTemporaryFile(mode="wb", delete=False,
                                         dir=destination.parent, prefix=f".{destination.name}.",
                                         suffix=".tmp") as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except OSError as exc:
        raise CampaignSaveError(f"캠페인 저장 실패: {exc}") from exc
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def load_campaign(path) -> CampaignState:
    try:
        return deserialize_campaign(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        raise CampaignSaveError(f"캠페인 저장 파일 읽기 실패: {exc}") from exc
