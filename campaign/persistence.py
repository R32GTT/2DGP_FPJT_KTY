"""JSON 저장·복원. 로드 자체는 시간·난수·작전을 갱신하지 않는다."""

from dataclasses import asdict, fields
import json
import os
from pathlib import Path
import tempfile

from .models import (CampaignState, Event, ExternalDemand, ExternalSource, Facility,
                     Ledger, Node, Operation, Position, Region, Reservation, Road, Unit, Vehicle)
from .resources import assert_invariants


SAVE_VERSION = 1
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
        for key, cls in (("roads", Road), ("regions", Region), ("facilities", Facility),
                         ("operations", Operation), ("reservations", Reservation)):
            row[key] = {key: _construct(cls, value) for key, value in row[key].items()}
        row["nodes"] = {key: _construct(Node, {**value, "position": _construct(Position, value["position"])})
                        for key, value in row["nodes"].items()}
        for key, cls in (("units", Unit), ("vehicles", Vehicle)):
            row[key] = {key: _construct(cls, {**value, "position":
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
    text = serialize_campaign(state)
    destination = Path(path)
    temporary = None
    try:
        # 완성된 임시 파일만 교체해 저장 도중 실패가 기존 세이브를 훼손하지 않게 한다.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False,
                                         dir=destination.parent, prefix=f".{destination.name}.",
                                         suffix=".tmp") as stream:
            temporary = Path(stream.name)
            stream.write(text)
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
