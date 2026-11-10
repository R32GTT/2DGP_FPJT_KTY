# Gameplay Vertical Slice

생성된 월드 위에서 플레이어 이동/사격과 더미 적 AI를 검증하는 런타임 모듈.

## 실행

```powershell
& .\.venv\Scripts\python.exe -m gameplay --seed 2026
& .\.venv\Scripts\python.exe -m gameplay --seed 2026 --no-play
```

조작: `WASD` 이동, `Shift` 달리기, 마우스 조준, `LMB` 사격, `F1` 경로 디버그,
`ESC` 종료.

## 구현

- `NavigationWorld`
  - 실외: 생성된 0.5m grid A*.
  - 시설 내부: maptool이 저작한 `nav_node`/`nav_edge` Dijkstra.
  - 시설 입구에서 `outdoor <-> facility` 도메인 전환.
  - authored graph가 없으면 grid 경로로 폴백.
- 더미 적
  - `patrol -> investigate -> chase -> search`.
  - 시야: 거리 + occlusion grid line-of-sight.
  - 총성 noise event로 주변 적이 조사.
  - 시설 문을 통해 플레이어 추적.
- 플레이어
  - PRD 기준 표시 `48x24px`, 충돌 반경 `0.30m`.
  - hitscan 사격, HP/Armor, 사망 시 스폰 재시작.

## 연결 메모

`worldgen`은 맵 데이터만 만든다. `gameplay`는 그 데이터를 소비하는 별도 런타임이며,
나중에 실제 게임 루프가 붙을 때 이 모듈의 `NavigationWorld`, `Player`, `Enemy`를
그대로 가져다 쓰면 된다. 맵툴/월드젠/게임플레이 사이에 별도 좌표 변환을 만들지 않는다.
