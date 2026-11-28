# AI 수색 개선 핸드오프 (2026-11-28)

## 다음 채팅에서 바로 시작할 내용

이 저장소의 `codex/indoor-search` 브랜치에서 실내 수색 1차 개선을 마쳤다. 다음 작업은 **실외 산개·사격과 추적 기억 문제를 각각 재현하고 고치는 것**이다. 먼저 아래의 기존 실패를 기준으로 현 상태를 확인하고, 행동 변경과 성능 변경을 서로 다른 한국어 커밋에 담는다. 사용자가 명시적으로 무시하라고 한 킬하우스를 최종 품질 기준으로 삼지 않는다.

## 사용자 목표와 제약

- 목표 행동은 Intravenous 2처럼 단서에 맞춰 그럴싸하게 반응하는 AI다. 방을 구석구석 확인하되, 소리만 들은 병사가 플레이어의 정확한 현재 위치를 알면 안 된다. 직접 본 병사는 마지막 목격 위치를 기억하며, 무전 보고를 받은 병사는 **보고 시각의 위치**를 알 수 있다.
- 실내외 판단은 가능한 한 공통으로 두되, 실내의 문·방·가구·좁은 사선은 공간 데이터와 행동 제약으로 반영한다. 같은 상황에서 모든 병사가 똑같이 행동하지 않도록 한다.
- 조명은 화면과 인식 속도에 일부 연결돼 있지만, 소등 이벤트 및 조명 반응은 조명 시스템의 향후 형태가 정해지지 않아 **이번 작업 범위에서 제외**했다. 조명 제어 기능을 임의로 만들지 않는다.
- 프리팹의 정적 수색 공간 정보는 제작 단계에 계산하고, 배치 시 좌표만 변환한다. 새 기능을 위해 AI 코드를 매번 전면 개편하는 구조는 피한다.
- 성능 원칙: 반복 작업 묶기, 반복 순회 줄이기, 캐시 효율 향상, 독립 작업 병렬화, CPU 병목이 측정될 때 GPU 계산 셰이더 검토. 병렬화와 GPU는 비용을 측정한 뒤 적용한다.
- 코드는 사용자의 명시적 지시로 개발을 시작했다. **기능별로 커밋을 분리하고 커밋 메시지는 한국어**로 쓴다. 성능 개선과 행동 변경을 한 커밋에 묶지 않는다.

## 작업 브랜치와 완료 커밋

브랜치: `codex/indoor-search` (기준은 `main`). 아직 병합하지 않았다.

| 커밋 | 내용 |
| --- | --- |
| `15c0cbf` | 실내 수색 중 약한 실외 소리로 인한 중단 억제 |
| `da55337` | 프리팹 수색 구역을 미리 계산해 배치 시 재사용 |
| `0373f57` | 실내 시선 선택의 반복 시야 점수 계산 축소 |
| `b9a5e5e` | 프리팹 관찰 위치로 방의 미확인 영역 수색 |
| `1e8ccae` | 병사별 관찰 위치와 확인 속도에 안정적인 차이 부여 |

주요 구현 위치:

- `prototype/ai_search.py`: 약한 실외 소리의 전환 유예, 프리팹 관찰 위치 선택, `Visit`의 개인별 확인 시간.
- `prototype/indoor_search.py`: 수색 영역, 구운 관찰 위치·가시 범위 로딩, 각도 정렬과 누적합을 이용한 `choose_look`, 실시간 확인 판정.
- `prototype/search_bake.py`: 프리팹 로컬 좌표의 방·표본·관찰 위치·정적 가시 범위를 계산 및 배치.
- `prototype/prefabs.py`: 프리팹의 `search_bakes/*.json`을 원본 해시로 검증해 사용. 파일이 없거나 오래되면 기존 런타임 계산으로 돌아간다.
- `tools/bake_search_prefabs.py`: 프리팹을 수정한 뒤 `.venv\Scripts\python.exe tools/bake_search_prefabs.py`로 구운 파일을 갱신한다.
- `prototype/data/prefabs/search_bakes/`: 현재 통신시설·본부·창고의 구운 데이터.

정적 가시 범위는 관찰 위치 **선택 점수**에만 사용한다. 확인 완료는 현재 시야로 판정한다. 월드의 장애물 revision이 바뀌면 관찰 위치 점수도 실시간 시야 계산으로 돌아간다. 연막 등 동적 가림과 다른 프리팹 경계 조건은 추가로 검증할 가치가 있다.

## 검증 및 측정

- `tests.test_prototype_indoor_search`와 `tests.test_prototype_prefabs`: 합계 20개 통과. 프리팹 3종의 회전 배치 후 수색 영역이 런타임 생성 결과와 일치하는지, 관찰 위치가 방의 표본을 모두 덮는지 확인했다.
- 생성 맵 시드 7, 1개 소형 지역: 창고의 수색 표본 256개를 관찰 위치 7개로 계획. 병사 1명이 시뮬레이션 73.23초에 256개를 전부 확인했다. 이는 완료 여부 검증이며 플레이 체감 품질 평가는 아니다.
- 창고 프리팹 `World` 생성 30회 중앙값: 런타임 공간 생성 13.39ms, 구운 데이터 2.29ms. 테스트 머신의 소규모 반복 측정이다.
- `choose_look` 192개 후보·300회 순수 선택 측정: 기존 1,205ms, 변경 후 102ms. 실게임 프레임 시간과 동일시하지 않는다.
- 프로파일러: `reports/2026-11-28_18-20-58-410960/report.md` (시설 수색 훈련 5명, 20초), `reports/2026-11-28_18-21-08-623647/report.md` (생성 맵 28명, 15초). 전자는 `EnemyAI.update` inclusive 약 2.13초, `choose_look` 약 0.19초, `STRtree.query` 약 0.52초 표본 추정이다. 두 보고서는 다른 장면이므로 전후 성능 비교로 사용하지 않는다.
- `tests.test_prototype_ai`는 실패 7건과 오류 4건이 남아 있다. 이전 조사에서도 확인한 실외 산개·사격·가시성 관련 기존 실패다. 현 브랜치의 신규 실내·프리팹 테스트는 통과한다.

검증 명령:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_prototype_indoor_search tests.test_prototype_prefabs
.venv\Scripts\python.exe -m unittest tests.test_prototype_ai
.venv\Scripts\python.exe -m prototype.profiler -- --scene prototype/scenarios/ai_facility.json --headless --frames 1200 --search-drill
.venv\Scripts\python.exe -m prototype.profiler -- --generate 7 --regions 1 --headless --frames 900
```

`--generate`와 `--search-drill` 조합은 현재 지원되지 않는다. 프로파일러 래퍼가 내부 게임 예외에도 종료 코드 0을 반환할 수 있으므로 출력의 `Traceback` 유무를 확인한다.

## 다음 작업의 권장 순서

1. **실외 산개와 사격선 재현.** `tests/test_prototype_ai.py`의 분대 접근, 좁은 통로 이후 산개, 사격선 열림 테스트를 각각 실행한다. `prototype/ai_squad.py`, `prototype/squad_approach.py`, `prototype/ai_combat.py`, `prototype/ai_crowd.py`를 보고, 목표점 예약과 사격선 확보가 같은 분대 계획에서 충돌하는지 확인한다. 행동 수정은 별도 커밋으로 한다.
2. **추적 기억 재현.** `prototype/ai_state.py:238`의 `Situation`, `prototype/ai.py:311`의 수색, `prototype/ai.py:417`의 순찰 전환을 추적한다. `prototype/ai_squad.py:191`의 집결 종료는 `clear_contact()`로 마지막 목격·보고 정보를 지우며, 순찰 전환도 같은 함수를 호출한다. 이것이 사용자가 말한 조기 포기의 실제 원인인지 시나리오로 확인한 뒤, 개인 목격·무전 보고·소리 단서의 수명과 수색 종료 조건을 분리한다. 정보가 오래됐다고 플레이어의 현재 좌표를 아는 식으로 처리하지 않는다.
3. **실내 전환 유예 검증.** 약한 실외 소리를 수색 완료까지 미루는 현재 정책을 실제 플레이에서 확인한다. 강한 새 단서·총성은 즉시 반응해야 한다. 긴 수색 중 소리가 너무 오래 묵는다면 현재 방 완료 또는 최대 유예 시간 기준으로 조정하고 회귀 테스트를 추가한다.
4. **성능 후속.** 동일 장면·동일 시드로 변경 전후를 프로파일링한다. `py_trees`는 `ActorDecision`의 `Selector(memory=False)`가 매 틱 우선순위를 다시 평가하므로, 불필요한 반복과 행동 중단 여부를 살핀다. `sample_views`와 Shapely 시야 조회는 묶음 처리·공유 캐시 후보이다. 병렬화와 GPU는 실제 병목 및 전송 비용을 비교한 뒤 결정한다.

## 작업 트리 주의

마지막 확인 시 `.gitignore`의 수정과 `main.py`, `fonts/previews/*.png`의 미추적 파일은 **사용자의 다른 작업**이었다. 이 브랜치 커밋에 포함하지 않았고, 다음 채팅에서도 덮어쓰거나 일괄 추가하지 않는다. `reports/`는 Git에서 무시되므로 이 핸드오프 문서만 강제 추적한다.
