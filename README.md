# Revenant : L.O.S.T

적 후방의 자율 군수망과 플레이어 개입 결과를 지속 상태로 관리하는 프로젝트.
현재 실행 가능한 캠페인 범위는 **자율 군수 v0.1 코어**다.

- 게임 규칙: [PRD.md](PRD.md)
- 구현 계획·인계: [CAMPAIGN_IMPLEMENTATION.md](CAMPAIGN_IMPLEMENTATION.md)
- 기존 시설 제작 도구: [maptool/README.md](maptool/README.md)

## 개발 환경

캠페인은 Python 표준 라이브러리만 사용한다. Python 3.13.15에서 실행·검증했다.
콘솔 실행과 테스트에는 Pico2D나 추가 패키지 설치가 필요하지 않다.
아래 명령은 저장소 루트에서 실행한다.

```powershell
py -3.13 -m campaign --scenario campaign/scenarios/rear_test.json --hours 24 --support finite --seed 42
py -3.13 -m campaign --hours 24 --support infinite --seed 42
py -3.13 -m unittest discover -s tests -p "test_campaign_*.py"
```

새 캠페인의 기본 실행 시간은 24게임시간, 기본 시드는 42, 기본 지원 모드는 `finite`다.
실행 결과는 세계 시각·작전 상태별 개수·재고·소비·출하·손실을 담은 JSON이다.
`--hours` 대신 `--seconds 100`으로 짧은 구간을 실행할 수 있다.

## RTS형 맵 비주얼라이저

Windows에서는 `CampaignViewer.bat`를 더블클릭한다. 터미널에서는 다음 명령을 사용한다.

```powershell
py -3.13 -m campaign.visualizer
py -3.13 -m campaign.visualizer --support infinite --speed 600
py -3.13 -m campaign.visualizer --load campaign-save.json --paused
```

Python 기본 Tkinter로 실행한다. 추가 패키지 설치가 필요하지 않다.
기본 배속은 120배이며 중요도가 가장 높은 지역을 확대한 상태로 시작한다.

- 탑다운 지면·수목·도로·창고·HQ·통신시설과 주둔 병력 그룹·주차 차량을 표시한다.
- 실제 수송대의 위치·방향·차량 수·상태를 표시하고, 선택한 수송대의 경로를 강조한다.
- 시설별 재고·예약·목표량, 수송대 화물·유지 물자·차량·호위 ID와 최근 세계 사건을 조회한다.
- 시설·수송대·도로를 선택해 물자 탈취·파괴, 시설 피해, 차량 격파, 호위 손실, 도로 차단·개방, 귀환을 입력한다.
  모든 개입은 `Simulation.apply_action()`을 사용한다. 화물량은 물자 종류·수량 입력으로 지정한다.
- 수송대의 차량 격파·호위 손실 버튼은 목록상 첫 번째 생존 차량·병력 그룹을 대상으로 한다.
- 저장·불러오기 버튼은 일시정지 상태로 처리한다. 콘솔과 같은 캠페인 저장 파일을 사용한다.

| 조작 | 동작 |
| --- | --- |
| 좌클릭 | 시설 부지·수송대·도로 선택 |
| 더블클릭 | 클릭한 위치 확대. 목록에서는 선택한 항목으로 이동 |
| `F` | 선택한 대상 확대. 미선택 시 주요 지역 보기 |
| 우클릭·중클릭 드래그 | 카메라 이동 |
| 휠 | 마우스 위치를 중심으로 확대·축소 |
| 지도에서 방향키·`WASD` | 카메라 이동 |
| 미니맵 클릭 | 해당 지점으로 카메라 이동 |
| `Home` | 전체 물류망 보기 |
| `Space` | 재생·일시정지. 입력 위젯에서는 해당 위젯의 기본 조작 유지 |
| 1초 진행 | 일시정지 상태에서 전략 틱 하나 진행 |
| +1분·+1시간·+6시간 | 실제 사건을 처리하며 해당 시간만큼 진행 |
| 스킵 취소 | 현재 시각에서 추가 진행 취소 |

큰 스킵은 최대 30게임초씩 나눠 처리하며 프레임 사이에 지도 입력을 처리한다.
지형과 건물은 카메라·외형 변경 때 다시 그리고, 차량 위치와 선택 표시는 매 프레임 갱신한다.
저배속의 같은 도로 구간 이동은 화면에서 보간하며, 보간은 캠페인 상태를 변경하지 않는다.

시설 부지·건물·수목·도로 폭과 지역 점선 영역은 디버그용 벡터 외형이다.
실제 시설 프리팹의 내부·충돌·영토 경계와의 연결은 후속 단계다.
수송대의 차량·주둔 그룹은 같은 실제 ID를 표시하며, 표시 슬롯을 위해 화면에서만 간격을 둔다.
보기용 난수는 캠페인 난수와 분리되어 지도 조작·그리기가 작전 결과를 바꾸지 않는다.

GUI 검증을 포함한 테스트 실행:

```powershell
$env:CAMPAIGN_GUI_TEST = "1"
py -3.13 -m unittest discover -s tests -p "test_campaign_*.py"
```

GUI 테스트에는 데스크톱 화면이 필요하다. 환경 변수를 지정하지 않으면 GUI 6개만 건너뛴다.

## 저장·복원

```powershell
py -3.13 -m campaign --hours 6 --save campaign-save.json
py -3.13 -m campaign --load campaign-save.json
py -3.13 -m campaign --load campaign-save.json --hours 6 --save campaign-save.json
```

- `--load`만 지정하면 저장 시각의 상태만 조회한다. 시간이 흐르거나 작전이 다시 생성되지 않는다.
- `--load`와 진행 시간을 함께 지정하면 **저장 시각부터 추가로** 진행한다.
- 불러온 시드·지원 정책·월드는 유지한다. `--load`와 `--seed`·`--support`·`--scenario`는 함께 사용할 수 없다.
- 저장 경로의 부모 폴더는 이미 존재해야 한다. 저장은 임시 파일 완성 후 원자적으로 교체한다.
- 파일 부재·손상·버전 불일치는 오류 메시지와 종료 코드 2로 알린다.

## 디버그 콘솔

```powershell
py -3.13 -m campaign --interactive
py -3.13 -m campaign --load campaign-save.json --interactive
```

새 콘솔은 시각 0에서 초기 수요를 판단한 뒤 입력을 기다린다.
콘솔은 명시적인 `advance` 명령으로 시간을 진행하는 검증 도구다.

```text
status
facilities
operations
operation operation_1
assets
history 10
advance 100
action {"type":"block_road","road_id":"a_b","blocked":true}
action {"type":"capture","operation_id":"operation_1","amount":{"supplies":5}}
action {"type":"destroy_stock","owner_id":"depot_a","amount":{"ammunition":10}}
action {"type":"damage_facility","facility_id":"hq_b","damage":100}
action {"type":"destroy_vehicle","vehicle_id":"a_truck_1"}
action {"type":"damage_unit","unit_id":"a_escort_1","soldiers":1}
action {"type":"return_operation","operation_id":"operation_1"}
save campaign-save.json
quit
```

ID·화물량은 `operations`, `operation ID`, `assets`, `facilities`에서 확인한다.
위 행동은 각각 독립적인 사용 예시이며, 현재 상태에 맞는 대상과 수량을 지정해야 한다.
`capture`·`destroy_stock`은 `owner_id`와 `operation_id` 중 하나를 사용한다.
집결 중 화물은 아직 출발지 재고이므로 해당 재고에서 처리한다.
무한 외부 예비 물자는 출발 시 실제 화물이 된 뒤 개입할 수 있다.
병력 손실량은 현재 병력 이하의 양의 정수다.
귀환 명령은 미출발 작전의 예약을 취소하거나, 출발한 수송대를 실제 경로로 되돌린다.

### 시각별 개입 재현

다음 형식의 JSON을 `actions.json`에 저장한다. 시각은 **절대 세계 초**다.
같은 시각의 행동은 목록 순서대로 적용한다.

```json
[
  {"time":100,"action":{"type":"block_road","road_id":"a_b","blocked":true}},
  {"time":3600,"action":{"type":"block_road","road_id":"a_b","blocked":false}}
]
```

```powershell
py -3.13 -m campaign --hours 24 --actions actions.json --seed 42
```

행동 시각은 현재 시각부터 이번 실행의 종료 시각 사이에서 오름차순이어야 한다.
해당 시각의 이동·예정 사건 처리 이후 행동을 적용한다.

## 코어 연결 API

```python
from campaign.persistence import load_campaign, save_campaign
from campaign.scenario import load_scenario
from campaign.simulation import Simulation

simulation = Simulation(load_scenario(seed=42, support="finite"))
simulation.advance_to(100)  # 절대 세계 시각, 정수 초
simulation.apply_action({"type": "block_road", "road_id": "a_b", "blocked": True})
simulation.advance_to(21600)
save_campaign(simulation.state, "campaign-save.json")
restored = Simulation(load_campaign("campaign-save.json"))
```

`Simulation` 생성과 `load_campaign()`은 상태를 진행시키지 않는다.
일반 진행·배속·스킵은 `advance_to()`의 같은 1초 이동·사건 처리 경로를 사용한다.
매 초 이동을 먼저 처리하고, 예정 사건은 소비(10) → 인계(20) → 외부 수요(30) → 계획(40),
같은 우선순위에서는 발급 순번으로 처리한다.

## 검증 월드·운영 규칙

`campaign/scenarios/rear_test.json`에서 고정 월드와 임시 운영 수치를 조정한다.

- 소형 지역 3개. 지역마다 HQ·통신시설·창고, 외부 진입 지점·출하 출구·우회 도로 포함.
- 거리·속도는 m·m/s, 위치는 실수 `(x, y, floor)`, 시간은 정수 초, 물자는 정수 단위다.
- 물자 3종은 `supplies`, `ammunition`, `repair_parts`. `resource_priority`가 적재 순서를 정한다.
- 현지 수요를 먼저 처리하고, 집적 창고는 미충족 외부 출하 수요와 운행 예비량을 추가로 확보한다.
- 예약은 재고를 옮기지 않는다. 실제 출발 때 차감하고 도착·하역 후 목적지에 인계한다.
- 최소 주둔·비축, 차량 적재량, 차량당 호위 조건을 확인한다. 유지 물자도 적재량에 포함된다.
- 주둔·수송 호위 유지와 도로 구간 진입에 보급품을 소비한다. 평시 탄약을 임의 소모하지 않는다.
- 차단은 수송대가 현장에서 발견한다. 도로 중간에서는 실제로 되돌아간 뒤 우회·대기·귀환한다.
- 출하는 실제 출구 도착·인계 이후 집계한다. 차량·호위는 회차 대기와 실제 귀환을 마쳐야 다시 가용하다.
- 유한 모드는 외부 예비 물자를 실제 출발량만큼 차감한다. 무한 모드는 예비 물자 고갈을 적용하지 않는다.
  두 모드 모두 실제 차량·호위 풀, 출발 간격, 화물 상한, 도로 이동·귀환 제약을 유지한다.
- 원거리 다차량 화물은 적재 용량 비율로 분산된 것으로 취급한다. 차량 격파 시 해당 비율의 화물·유지 물자를 손실 처리한다.
  잔해 위치와 살아남은 호위·차량의 ID·위치·상태는 보존한다.

회계는 초기 실재량과 외부 유입을 시설·외부 유한 예비량·화물·유지 물자·플레이어 보유량,
누적 소비·파괴·외부 인도·무한 외부로의 잔여 귀환량과 대조한다.
탈취량은 플레이어 보유량으로 이동하며 외부 인도·파괴와 중복 집계하지 않는다.
저장에는 사건 큐·순번·난수 진행·다음 ID·예약·경로 진행·손실·정책을 포함한다.

## 검증 결과와 다음 연결

2026-11-13 기준 캠페인·비주얼라이저 테스트 **63개 통과**. 실제 Tk GUI 검증 6개 포함.
유한·무한 모드 각각 무개입 24시간, 경로 차단·탈취·손실, 자원 보존, 시간 진행 방식 일치,
집결·이동·하역·회차·귀환 중 저장과 재개 일치, 오류 저장 처리, 콘솔·행동 스크립트를 검증했다.
뷰어에서는 지도 선택·시간 조작·개입·저장 복원·카메라 이동과 코어 상태 일치를 검증했다.
시드 42의 24시간 실행에서 유한 모드는 보급품 840, 무한 모드는 보급품 1320을 외부에 인도했다.

다음 연결 대상은 관측·보고·통신 기반 HQ 판단, 순찰·재배치·QRF·복구,
유지 능력 기반 해방·군수 기반 탈환, 실제 월드와 Pico2D 표현이다.
이번 군수 코어 검증은 PRD 1단계 전체나 다이나믹 캠페인 전체 완료를 의미하지 않는다.
