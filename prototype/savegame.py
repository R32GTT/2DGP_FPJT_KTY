"""세이브 슬롯이 공유 월드와 해당 시점의 상태를 참조한다."""

from hashlib import sha256
import json
from pathlib import Path
from zlib import error as CompressionError

from campaign.persistence import CampaignSaveError, campaign_payload, restore_campaign
from campaign.resources import TERMINAL_PHASES
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
AUTO_SAVE = DEFAULT_SAVE.with_name("autosave.json.gz")
MANUAL_SAVES = tuple(DEFAULT_SAVE.with_name(f"slot_{index}.json.gz") for index in range(1, 7))
MAX_BYTES = 16 * 1024 * 1024
ARCHIVE_PAGE_ROWS = 256


def save_slots():
    slots = [("자동 저장", AUTO_SAVE)]
    slots.extend((f"슬롯 {index}", path) for index, path in enumerate(MANUAL_SAVES, 1))
    if DEFAULT_SAVE.is_file():
        slots.append(("기존 저장", DEFAULT_SAVE))
    if QUICK_SAVE.is_file():
        slots.append(("빠른 저장", QUICK_SAVE))
    return tuple(slots)


def latest_save():
    existing = (path for _, path in save_slots() if path.is_file())
    return max(existing, key=lambda path: path.stat().st_mtime, default=None)


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


def _archive_pages(files, rows):
    pages = []
    for start in range(0, len(rows), ARCHIVE_PAGE_ROWS):
        data = json_bytes(rows[start:start + ARCHIVE_PAGE_ROWS])
        digest = sha256(data).hexdigest()
        _save_once(files.archive_path(digest), data, MAX_BYTES)
        pages.append(digest)
    return pages


def _archive_campaign(files, campaign):
    state = campaign["state"]
    archived = [(index, key, op) for index, (key, op) in enumerate(state["operations"].items())
                if op["phase"] in TERMINAL_PHASES]
    for _, key, _ in archived:
        del state["operations"][key]
    return {"operations": _archive_pages(files, archived),
            "history": _archive_pages(files, state.pop("history"))}


def _restore_archive(files, campaign, archive):
    state = campaign["state"]
    history = []
    for digest in archive["history"]:
        history.extend(_read_verified(files.archive_path(digest), digest, MAX_BYTES))
    state["history"] = history
    ended = {}
    keys = set(state["operations"])
    for digest in archive["operations"]:
        for index, key, op in _read_verified(files.archive_path(digest), digest, MAX_BYTES):
            if (type(index) is not int or index < 0 or index in ended or key in keys
                    or op["phase"] not in TERMINAL_PHASES):
                raise ValueError("저장한 종료 작전 상태가 잘못됐습니다")
            ended[index] = key, op
            keys.add(key)
    if any(index >= len(keys) for index in ended):
        raise ValueError("저장한 종료 작전 순서가 잘못됐습니다")
    active = iter(state["operations"].items())
    state["operations"] = dict(ended[index] if index in ended else next(active) for index in range(len(keys)))


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
        campaign = campaign_payload(play.campaign.simulation.state)
        archive = _archive_campaign(files, campaign)
        state = json_bytes({"campaign": campaign, "archive": archive,
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
            if "archive" in state:
                _restore_archive(files, state["campaign"], state["archive"])
        json.dumps(state, allow_nan=False)
        campaign = Simulation(restore_campaign(state["campaign"]))
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
        play.message = "월드 복원 완료 · 저장과 불러오기 사용 가능"
        return play
    except (OSError, EOFError, ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError, CompressionError) as error:
        raise CampaignSaveError(f"플레이 불러오기 실패: {error}") from error
