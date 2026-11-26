"""세이브 슬롯이 공유 월드와 해당 시점의 상태를 참조한다."""

from hashlib import sha256
import json
from pathlib import Path
from zlib import error as CompressionError

from campaign.persistence import CampaignSaveError, deserialize_campaign, serialize_campaign
from campaign.simulation import Simulation
from .generated_world import GENERATION_VERSION, WORLD_VERSION, generate_world
from .save_state import capture, restore
from .simulation import PlaySimulation
from .throwables import DATA_PATH as THROWABLE_DATA_PATH
from .world import ROOT
from .world_files import MAX_WORLD_BYTES, WorldFiles, json_bytes, read_bytes, write_bytes


SAVE_VERSION = 7
LEGACY_SAVE_VERSION = 6
SAVE_FORMAT = "revenant_lost.generated_play"
DEFAULT_SAVE = ROOT / "saves" / "prototype.json.gz"
QUICK_SAVE = DEFAULT_SAVE.with_name("quick.json.gz")
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


def _save_once(path, data, limit):
    if len(data) > limit:
        raise ValueError("저장 데이터 크기 제한 초과")
    if not path.is_file():
        write_bytes(path, data)


def _read_verified(path, digest, limit):
    data = read_bytes(path, limit)
    if sha256(data).hexdigest() != digest:
        raise ValueError("월드 파일 내용이 저장 슬롯과 일치하지 않습니다")
    return json.loads(data)


def save_game(play, path=DEFAULT_SAVE):
    generation = play.world.data.get("generation", {})
    if "region_count" not in generation:
        raise CampaignSaveError("저장은 절차 생성 월드에서 사용할 수 있습니다")
    try:
        path = Path(path)
        scene = play.world.source
        if isinstance(scene, Path):
            scene = json.loads(scene.read_text(encoding="utf-8"))
        world = json_bytes(scene)
        files = WorldFiles(path.parent / "worlds", sha256(world).hexdigest())
        state = json_bytes({"campaign": json.loads(serialize_campaign(play.campaign.simulation.state)),
                            "local": capture(play), "changes": play.world.collider_changes()})
        revision = sha256(state).hexdigest()
        _save_once(files.scene_path, world, MAX_WORLD_BYTES)
        _save_once(files.snapshot_path(revision), state, MAX_BYTES)
        chunks = play.world.chunks
        if chunks and (chunks.files is None or chunks.files.directory != files.directory):
            for key, chunk in chunks.loaded.items():
                if not files.chunk_path(key).is_file():
                    files.cache_chunk(key, chunk)
        # 참조 대상이 모두 기록된 뒤 슬롯을 원자적으로 교체한다.
        write_bytes(path, json_bytes({"format": SAVE_FORMAT, "version": SAVE_VERSION,
                                      "content": content_version(), "world": files.world_id,
                                      "revision": revision}))
    except (OSError, ValueError, TypeError) as error:
        raise CampaignSaveError(f"플레이 저장 실패: {error}") from error


def load_game(path=DEFAULT_SAVE):
    try:
        path = Path(path)
        payload = json.loads(read_bytes(path, MAX_BYTES))
        if (payload["format"] != SAVE_FORMAT or type(payload["version"]) is not int
                or payload["version"] not in (LEGACY_SAVE_VERSION, SAVE_VERSION)
                or payload["content"] != content_version()):
            raise ValueError("현재 버전·데이터와 다른 저장본입니다")
        world_root = path.parent / "worlds"
        if payload["version"] == LEGACY_SAVE_VERSION:
            scene = generate_world(**payload["generation"])
            state = payload
        else:
            files = WorldFiles(world_root, payload["world"])
            scene = _read_verified(files.scene_path, files.world_id, MAX_WORLD_BYTES)
            generation = scene["generation"]
            if (generation["version"] != WORLD_VERSION or generation["region_version"] != GENERATION_VERSION):
                raise ValueError("지원하지 않는 월드·지역 생성 버전")
            revision = payload["revision"]
            state = _read_verified(files.snapshot_path(revision), revision, MAX_BYTES)
        json.dumps(state, allow_nan=False)
        campaign = Simulation(deserialize_campaign(json.dumps(state["campaign"])))
        position = state["local"]["player"]["position"]
        play = PlaySimulation(scene, campaign=campaign, materialized=False, world_root=world_root,
                              initial_position=(position["x"], position["y"]))
        if (set(campaign.state.nodes) != set(play.world.data["road_network"]["nodes"])
                or set(campaign.state.roads) != {road["id"] for road in play.world.roads}
                or any((node.position.x, node.position.y) != tuple(play.world.data["road_network"]["nodes"][key])
                       for key, node in campaign.state.nodes.items())):
            raise ValueError("생성 월드·캠페인 지도 불일치")
        play.world.restore_changes(state["changes"])
        restore(play, state["local"])
        play.message = "월드 복원 완료 · F5/F9 저장·불러오기 · F6/F8 빠른 저장·불러오기"
        return play
    except (OSError, EOFError, ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError, CompressionError) as error:
        raise CampaignSaveError(f"플레이 불러오기 실패: {error}") from error
