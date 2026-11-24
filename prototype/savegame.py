"""생성 설정·캠페인·현장을 압축 저장한다. 이전 형식은 변환하지 않는다."""

import gzip
from hashlib import sha256
import json
from pathlib import Path
from zlib import error as CompressionError

from campaign.persistence import CampaignSaveError, deserialize_campaign, serialize_campaign, write_save
from campaign.simulation import Simulation
from .generated_world import generate_world
from .save_state import capture, restore
from .simulation import PlaySimulation
from .throwables import DATA_PATH as THROWABLE_DATA_PATH
from .world import ROOT


SAVE_VERSION = 3
SAVE_FORMAT = "revenant_lost.generated_play"
DEFAULT_SAVE = ROOT / "saves" / "prototype.json.gz"
MAX_BYTES = 16 * 1024 * 1024


def content_version():
    paths = [ROOT / "prototype/data/campaign_setup.json", ROOT / "prototype/data/weapons.json",
             ROOT / "prototype/data/attachments.json"]
    if THROWABLE_DATA_PATH.is_file():
        paths.append(THROWABLE_DATA_PATH)
    paths += sorted((ROOT / "prototype/data/prefabs").glob("*.json"))
    digest = sha256()
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def save_game(play, path=DEFAULT_SAVE):
    generation = play.world.data.get("generation", {})
    if "region_count" not in generation:
        raise CampaignSaveError("저장은 절차 생성 월드에서 사용할 수 있습니다")
    try:
        payload = {"format": SAVE_FORMAT, "version": SAVE_VERSION, "content": content_version(),
                   "generation": generation, "campaign": json.loads(serialize_campaign(play.campaign.simulation.state)),
                   "local": capture(play),
                   "changes": play.world.collider_changes()}
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(data) > MAX_BYTES:
            raise ValueError("저장 데이터 크기 제한 초과")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_save(gzip.compress(data, mtime=0), path)
    except (OSError, ValueError, TypeError) as error:
        raise CampaignSaveError(f"플레이 저장 실패: {error}") from error


def load_game(path=DEFAULT_SAVE):
    try:
        with gzip.open(path, "rb") as stream:
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("저장 데이터 크기 제한 초과")
        payload = json.loads(data)
        json.dumps(payload, allow_nan=False)
        if (payload["format"] != SAVE_FORMAT or type(payload["version"]) is not int
                or payload["version"] != SAVE_VERSION or payload["content"] != content_version()):
            raise ValueError("현재 버전·데이터와 다른 저장본입니다")
        scene = generate_world(**payload["generation"])
        campaign = Simulation(deserialize_campaign(json.dumps(payload["campaign"])))
        play = PlaySimulation(scene, campaign=campaign, materialized=False)
        if (set(campaign.state.nodes) != set(play.world.data["road_network"]["nodes"])
                or set(campaign.state.roads) != {road["id"] for road in play.world.roads}
                or any((node.position.x, node.position.y) != tuple(play.world.data["road_network"]["nodes"][key])
                       for key, node in campaign.state.nodes.items())):
            raise ValueError("생성 월드·캠페인 지도 불일치")
        play.world.restore_changes(payload["changes"])
        restore(play, payload["local"])
        play.message = "저장한 월드 복원 완료 · F5 저장 / F9 불러오기"
        return play
    except (OSError, EOFError, ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError, CompressionError) as error:
        raise CampaignSaveError(f"플레이 불러오기 실패: {error}") from error
