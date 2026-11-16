# 실내 수색 AI 인계

- 기준은 `PRD.md` §6~7. 현재 작업 범위는 `prototype` 실내 수색이며 `maptool`·`worldgen`은 보류한다.
- 목표는 한 자리에서 보이는 방을 먼저 훑고, 가려진 곳이 남을 때만 이동하는 것이다. 걸을 때는 진행 방향과 시선이 달라도 된다.
- 구현은 `prototype/ai.py`와 `prototype/indoor_search.py`. `6c9b6b7` 이동 중 확인, `b7ecea9` 한 위치에서 방 수색, `bc228c4` 시선과 이동 방향 분리.
- 마지막 검증은 테스트 63개 통과, GUI 6개 건너뜀. 화면 없이 Killhouse 69/69곳을 14.1초에 확인했다. 실제 지역의 세 시설 동시 수색은 502/502곳을 확인했으나 52.7초에서 66.2초로 늦어졌다.

## 다음 작업

1. `PlayAIKillhouse.bat`에서 `F3`으로 수색 재현, `P`로 시작한다. `F1`과 `M`으로 옆을 보며 걷는 동작, 정지 스캔, 가려진 지점으로의 이동을 확인한다.
2. `PlayAIFacility.bat`와 `PlayFieldProvingGround.bat`에서도 확인한다. 동시 수색에서 늦어진 창고의 마지막 지점을 추적하고, 경로 실패 후 재배정에 걸리는 시간을 측정한다.
3. 개발 지시를 받으면 재현된 문제부터 수정한다. `git status`로 기존 QRF·수송·에셋 작업을 확인하고, 수정한 기능만 검증해 한국어 메시지로 커밋한다.

테스트는 `& .\.venv\Scripts\python.exe -B -m unittest tests.test_prototype_indoor_search`.
