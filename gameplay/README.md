# Gameplay Vertical Slice

생성된 월드 위에서 플레이어 이동/사격과 더미 적 AI를 검증하는 런타임 모듈.

## 실행

```powershell
& .\.venv\Scripts\python.exe -m gameplay --seed 2026
& .\.venv\Scripts\python.exe -m gameplay --seed 2026 --no-play
```

조작: `WASD` 이동, `Shift` 달리기, 마우스 조준, `LMB` 단발 사격, `R` 재장전,
`F1` 경로 디버그, `ESC` 종료.

## 구현

- `NavigationWorld`
  - 실외: 생성된 0.5m grid A*.
  - AI 반경만큼 clearance를 확보한 셀을 우선 사용. 좁은 문은 clearance 1/0으로 단계적 폴백.
  - 시설 내부: maptool이 저작한 `nav_node`/`nav_edge` Dijkstra.
  - 시설 입구에서 `outdoor <-> facility` 도메인 전환.
  - authored graph가 없으면 grid 경로로 폴백.
- 더미 적
  - `patrol -> investigate -> chase -> search`.
  - 시야: 거리 + occlusion grid line-of-sight.
  - 플레이어가 안 보이면 실제 위치로 재경로하지 않고 `last_seen`으로만 이동.
  - 총성 noise event로 주변 적이 조사. `F1`에서 총성 반경/조사 지점 표시.
  - 시설 문을 통해 플레이어 추적.
- 플레이어
  - PRD 기준 표시 `48x24px`, 충돌 반경 `0.30m`.
  - hitscan 사격, HP/Armor, 사망 시 스폰 재시작.
  - 총기 상태는 `Weapon`, 피해 처리는 기존 `Actor.apply_damage()` 사용.

## 연결 메모

`worldgen`은 맵 데이터만 만든다. `gameplay`는 그 데이터를 소비하는 별도 런타임이며,
나중에 실제 게임 루프가 붙을 때 이 모듈의 `NavigationWorld`, `Player`, `Enemy`를
그대로 가져다 쓰면 된다. 맵툴/월드젠/게임플레이 사이에 별도 좌표 변환을 만들지 않는다.

## 총기 설정

`gameplay/data/weapons.json` 수정 후 재실행한다. 최상위 키는 고유 총기 ID다.
같은 탄창식 단발 히트스캔 총기는 항목을 복사해 추가한다. 시작 총기 ID는
`GameViewer.__init__`의 `pistol_45`다. 무기 교체·인벤토리는 아직 구현하지 않는다.
연사·샷건 탄자·관통처럼 다른 발사 방식은 공통 로직을 확장한다.

- `magazine_capacity`: 약실을 제외한 탄창 용량. 시작은 가득 찬 탄창 + 약실 1발.
- `initial_reserve`: 임시 시작 예비탄. `fire_interval_s`: 최소 발사 간격(초).
- `damage_curve`: `[거리(m), 기본 피해]` 목록. 거리 증가순, 시작 거리 0m,
  마지막 피해 0. 지점 사이 선형 보간, 마지막 거리부터 피해 없음. 이후 Armor 적용.
- `reload_remove_s` + `reload_insert_s`: 약실 잔탄 재장전 1.3초.
  빈 총은 `reload_chamber_s` 0.4초를 추가해 총 1.7초.
- `reload_move_multiplier`: 재장전 중 이동 배율, 임시값 0.4.
- `noise_radius_m`: 총성 반경. 기존 30m 유지. 명중률·적 AI·임시 자동 부활도 유지.

## 재장전 동작

HUD 탄약은 `탄창 + 약실 / 예비탄`으로 표시한다. 좌클릭은 탄약·발사 대기 여부와
관계없이 재장전을 취소한다. 완료 단계는 유지하며 미완료 단계는 다음 R 입력에서 재시작한다.

탄창 제거 시 잔탄을 예비탄으로 돌려주고, 삽입 시 최대 탄창 용량만큼 가져온다.
약실 장전은 탄창에서 1발을 옮기므로 빈 총 재장전 직후는 `11+1`이다.
추가 재장전으로 `12+1`까지 채울 수 있다. 탄창 제거 후에는 약실 잔탄만 발사 가능하다.
