"""생성된 월드 데이터와 청크의 디스크 저장소."""

import gzip
from hashlib import sha256
import json
from pathlib import Path

from campaign.persistence import write_save


MAX_WORLD_BYTES = 64 * 1024 * 1024
MAX_CHUNK_BYTES = 1024 * 1024


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def read_bytes(path, limit):
    with gzip.open(path, "rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("월드 데이터 크기 제한 초과")
    return data


def write_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    write_save(gzip.compress(data, mtime=0), path)


class WorldFiles:
    def __init__(self, root, world_id):
        if (not isinstance(world_id, str) or len(world_id) != 64
                or any(c not in "0123456789abcdef" for c in world_id)):
            raise ValueError("잘못된 월드 ID")
        self.directory = Path(root) / world_id
        self.world_id = world_id

    @classmethod
    def for_scene(cls, root, scene):
        # 같은 시드라도 생성 옵션과 실제 도로·시설 배치가 다르면 캐시를 분리한다.
        return cls(root, sha256(json_bytes(scene)).hexdigest())

    def chunk_path(self, key):
        x, y = key
        return self.directory / "chunks-v1" / f"{x}_{y}.json.gz"
