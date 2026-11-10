# Worldgen MVP

Pico2D에서 바로 돌아가는 절차적 지역 생성 모듈. 타일 에셋 없이 의미 그리드와
단색 플레이스홀더로 게임 루프를 검증한다.

## 설치

프로젝트 `.venv`에 설치한다. `pico2d`와 `Pillow`만 사용한다.

```powershell
& .\.venv\Scripts\python.exe -m pip install -r requirements-worldgen.txt
```

## 실행

```powershell
# 생성 + 검증 + Pico2D 플레이스홀더
& .\.venv\Scripts\python.exe -m worldgen --seed 2026

# 생성 + 검증만
& .\.venv\Scripts\python.exe -m worldgen --seed 2026 --no-play

# 생성 결과를 Tiled .tmj로 저장 (maptool에서 열 수 있음)
& .\.venv\Scripts\python.exe -m worldgen --seed 2026 --no-play `
  --export build\worldgen\world.tmj
```

조작: `WASD` 이동, `Shift` 달리기, `F1` 디버그(시설 내비 노드/도로), `ESC` 종료.

## 현재 구현

- 0.5m(32px) 의미 그리드. `1m = 64px`, `1 타일 = 0.5m`.
- 결정론적 시드 생성. 같은 시드/버전은 같은 지형, 도로, 나무, 시설 배치.
- 지형: 풀/흙/바위 패치, 외곽 경계, 스폰, 월드 게이트.
- 도로: 필수 노드 MST + 순환 간선 1개. 2m coarse A* -> 6m 폭 도로 래스터화.
- 시설: `maptool`의 `.tmj` 또는 컴파일된 `runtime.json`을 완성형 프리팹으로 배치.
  - 저작 충돌 데이터가 없으면 `Architecture` 타일을 벽으로 간주하는 폴백.
  - 시설 출입구 앞 4m apron을 자동으로 연다.
  - 시설의 authored `nav_node`/`nav_edge`를 월드 좌표로 변환해 보존.
- 숲: 밀도 노이즈 + 최소 간격 산개. 나무 줄기는 이동 차단, 수관은 시야 차단.
- 검증: 스폰에서 모든 시설 입구/내비 노드/도로 노드로 BFS 도달 가능 여부.
  - 나무가 길을 막으면 생성된 나무만 제거해 연결을 복구한다. 시설 벽은 건드리지 않는다.
- `tmj_export`: `Ground`, `Collision`, `Occluder`, `Objects` 레이어로 내보내
  maptool에서 다시 열거나 컴파일할 수 있다.

## 데이터 방향

```text
maptool .tmj -> runtime.json -> facility prefab -> worldgen
worldgen -> semantic grid -> .tmj -> maptool
```

시설은 완성형 스탬프다. 실외 지형/도로는 모듈 소켓이 아니라 painter로 생성한다.
포털/소켓 개념은 사용하지 않는다.

## 제한

- 타일 렌더러는 아직 없다. `PlaceholderViewer`가 의미 셀을 색 사각형으로 그린다.
- AI, 전투, 차량, 다층 건물, 청크 스트리밍은 아직 없다.
- 시설 내부 AI는 authored nav graph를 사용할 예정이며, 현재는 데이터 로드/보존까지만 한다.
- `generation_ms`는 머신에 따라 달라지므로 결정론 비교에서 제외한다.
