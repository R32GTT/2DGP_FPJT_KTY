# Gameplay Vertical Slice

생성된 월드 위에서 플레이어 이동/사격과 더미 적 AI를 검증하는 런타임 모듈.

## 실행

```powershell
& .\.venv\Scripts\python.exe -m gameplay --seed 2026
& .\.venv\Scripts\python.exe -m gameplay --seed 2026 --no-play
```

조작: `WASD` 이동, `Shift` 달리기, 마우스 방향 지정, `RMB` 유지 조준,
`LMB` 단발 사격, `R` 재장전, `F1` 경로 디버그, `ESC` 종료.

## 구현

- `NavigationWorld`
  - 실외: 생성된 0.5m grid A*.
  - AI 반경만큼 clearance를 확보한 셀을 우선 사용. 좁은 문은 clearance 1/0으로 단계적 폴백.
  - 시설 내부: maptool이 저작한 `nav_node`/`nav_edge` Dijkstra.
  - 시설 입구에서 `outdoor <-> facility` 도메인 전환.
  - authored graph가 없으면 grid 경로로 폴백.
- 더미 적
  - `patrol -> investigate -> chase -> search`, 탄약 소진 시 `retreat`.
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
`GameViewer.__init__`의 `pistol_45`다. 무기 교체 UI·인벤토리는 아직 구현하지 않는다.
연사·샷건 탄자·관통처럼 다른 발사 방식은 공통 로직을 확장한다.

- `magazine_capacity`: 약실을 제외한 탄창 용량. 시작은 가득 찬 탄창 + 약실 1발.
- `initial_reserve`: 임시 시작 예비탄. `fire_interval_s`: 최소 발사 간격(초).
- `damage_curve`: `[거리(m), 기본 피해]` 목록. 거리 증가순, 시작 거리 0m,
  마지막 피해 0. 지점 사이 선형 보간, 마지막 거리부터 피해 없음. 이후 Armor 적용.
- `reload_remove_s` + `reload_insert_s`: 약실 잔탄 재장전 1.3초.
  빈 총은 `reload_chamber_s` 0.4초를 추가해 총 1.7초.
- `reload_move_multiplier`: 재장전 중 이동 배율, 임시값 0.4.
- `noise_radius_m`: 총성 반경. 기존 30m·임시 자동 부활 유지.

## Spread와 조준

각도는 조준선 기준 좌우 반각이다. 현재 spread 안에서 균일하게 방향을 뽑아
히트스캔 경로를 회전한다. 첫 발도 정확한 한 점에 고정되지 않으며 낙차는 없다.

- `spread_base_deg`: 기본 1도. `spread_per_shot_deg`: 발사 성공마다 0.25도 증가.
- `spread_max_deg`: 이동·조준 보정을 포함한 최종 상한 3도.
- `spread_recovery_delay_s`: 마지막 발사 후 0.25초 대기.
- `spread_recovery_deg_per_s`: 이후 초당 2도씩 발사 누적분만 회복.
- `aim_spread_multiplier`: 조준 시 기본 spread와 발사 누적분에 0.6배 적용.
- `aim_settle_s`: 우클릭 유지 시 0.25초에 걸쳐 조준 안정. 해제 시 즉시 일반 상태.
- `move_spread_deg_per_mps`: 실제 이동 속도 1m/s당 0.15도 추가.
- `sprint_spread_multiplier`: 달리기 중 이동 불이익을 3배 적용.

탄도는 발사 전 spread로 결정하며 누적 증가분은 다음 발부터 적용한다.
빈 총·발사 대기 중 실패한 클릭은 spread나 회복 대기를 갱신하지 않는다.
최대로 안정된 정지 조준도 0.6도이며, 달리기 조준은 기본값 기준 2.85도다.
벽에 막혀 실제로 이동하지 않으면 이동 불이익은 없다.

크로스헤어 간격은 현재 spread와 플레이어에서 마우스까지 거리에 비례한다.
HUD의 `SPREAD`는 현재 반각이다. 총구 위치·벽 검사·총구 섬광·반동 연출은 후속 범위다.

## 재장전 동작

HUD 탄약은 `탄창 + 약실 / 예비탄`으로 표시한다. 좌클릭은 탄약·발사 대기 여부와
관계없이 재장전을 취소한다. 완료 단계는 유지하며 미완료 단계는 다음 R 입력에서 재시작한다.

탄창 제거 시 잔탄을 예비탄으로 돌려주고, 삽입 시 최대 탄창 용량만큼 가져온다.
약실 장전은 탄창에서 1발을 옮기므로 빈 총 재장전 직후는 `11+1`이다.
추가 재장전으로 `12+1`까지 채울 수 있다. 탄창 제거 후에는 약실 잔탄만 발사 가능하다.

## 적 총기와 장착

플레이어와 적의 장착 슬롯은 공통 `Actor.weapon`이다.
`equip_weapon(new_weapon)`은 이전 `Weapon` 인스턴스를 반환하며 병과·총기 종류를 제한하지 않는다.
교체한 무기의 탄약·발사 대기·spread는 유지하고, 재장전은 완료 단계까지만 보존해 중단한다.
한 무기 인스턴스는 한 캐릭터만 장착한다. 보조무장·습득·교체 UI는 후속 범위다.

`gameplay/data/enemies.json`에서 병과별 시작 장비와 행동 수치를 조정한다.
병과는 시작 장비를 선택할 뿐 사용할 수 있는 무기를 제한하지 않는다.

- `starting_weapon_id`: 현재 `pistol_45`. 총기 설정을 공유하되 각 적의 탄약·상태는 독립적이다.
- `reaction_delay_s`: 최초 발견·재발견 후 사격까지 0.5초.
- `sight_range_m`: 기존 거리+LOS 시야 22m 유지. 시야각은 아직 적용하지 않는다.
- `hold_distance_m`: 목격 중 12m 이내에서는 접근을 멈추고 사격.
  짧은 사거리의 무기를 장착하면 무기 최대 사거리의 80% 이내까지 접근한다.
- `retreat_distance_m`: 탄약 소진·비무장 시 마지막 관측 위치에서 멀어지는 10m 경로 탐색.
  위협이 보이지 않고 마지막 관측 위치에서 시야 거리 이상 멀어지면 후퇴를 멈춘다.
  경로 실패 시 제자리에서 최대 초당 한 번 재시도한다. 탄약 있는 무기를 받으면 교전·수색으로 복귀한다.

직접 목격한 대상만 사격한다. 시야가 끊기면 마지막 관측 위치로 이동할 뿐 숨은 플레이어에게 쏘지 않는다.
탄창이 비면 같은 `Weapon`으로 재장전하며, 재장전 중 이동도 총기 설정대로 느려진다.
히트스캔·거리별 피해·spread·Armor 판정은 플레이어와 적이 동일하다. 기존 근접 피해는 제거했다.
적끼리 피해를 주지 않지만 동료 몸은 탄환을 막는다. 적 총성을 플레이어 위치 단서로 사용하지 않는다.
노란 궤적은 플레이어, 주황 궤적은 적 사격이다. F1에서 적 병과·무기·탄약·재장전·후퇴 상태를 확인한다.
