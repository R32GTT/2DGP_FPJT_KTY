"""플레이어가 확보한 작전 기록과 직접 본 위치를 보관한다."""

from dataclasses import dataclass, replace
import math

from campaign.models import QRF_OPERATIONS, TROOP_TRANSPORTS
from .interactions import nearby_facility


OPERATION_NAMES = {"supply": "보급 수송", "export": "외부 출하", "repair": "시설 복구", "equipment": "장비 수송",
                   "redeployment": "병력 재배치", "motor_qrf": "차량 증원", "qrf": "도보 증원", "withdrawal": "철수"}


@dataclass(frozen=True)
class OperationNote:
    operation_id: str
    source: str | None = None
    recorded_at: int | None = None
    departure_at: int | None = None
    departed: bool = False
    route: tuple[str, ...] = ()
    quantity: int | None = None
    observed_at: int | None = None
    observed_position: tuple[float, float] | None = None


def collect(play):
    if not play.presence or play.player.hp <= 0:
        return
    player, world = play.player, play.world
    if play.time < player.blinded_until:
        play.message = "확인할 수 있는 작전 기록이나 차량이 없습니다"
        return
    state = play.campaign.simulation.state
    site = nearby_facility(world, player.xy)
    if (site and site["kind"] in {"warehouse", "hq"}
            and state.facilities[site["campaign_id"]].damage < 100
            and state.nodes[site["service_node"]].position.floor == player.position.floor
            and world.geometry.sight_clear(player.xy, site["terminal"], floor=player.position.floor)):
        key = site["campaign_id"]
        departures = {event.operation_id: event.time for event in state.events if event.kind == "depart"}
        count = 0
        for operation in state.active_operations():
            if (operation.kind not in OPERATION_NAMES or operation.phase not in {"loading", "outbound", "unloading", "working"}
                    or key not in {operation.origin_id, operation.destination_id}):
                continue
            note = play.operation_notes.get(operation.id, OperationNote(operation.id))
            quantity = None
            if operation.kind in TROOP_TRANSPORTS | QRF_OPERATIONS:
                quantity = sum(state.units[uid].soldiers for uid in operation.unit_ids)
            elif operation.kind == "equipment":
                reservation = state.reservations.get(operation.id)
                quantity = (reservation.cargo if reservation else operation.cargo)["night_gear"]
            departed = operation.departed_at is not None
            play.operation_notes[operation.id] = replace(
                note, source=key, recorded_at=state.world_time, departed=departed,
                departure_at=operation.departed_at if departed else departures.get(operation.id),
                route=tuple(operation.route), quantity=quantity)
            count += 1
        if count:
            play.message = f"작전 기록 {count}건 확보 · I 정보 노트"
            return

    if player.position.floor != 0:
        play.message = "확인할 수 있는 작전 기록이나 차량이 없습니다"
        return
    sky = play.sky
    reach = 10 + 18 * sky.daylight
    if player.night_vision or player.lamp == "white":
        reach = max(reach, 17)
    nearby = []
    for truck in play.transports.trucks:
        point = (truck.x, truck.y)
        distance = math.dist(player.xy, point)
        if distance <= reach and world.geometry.sight_clear(player.xy, point, floor=0):
            nearby.append((distance, truck.id, truck))
    if not nearby:
        play.message = "확인할 수 있는 작전 기록이나 차량이 없습니다"
        return
    truck = min(nearby)[2]
    key = truck.convoy.id
    note = play.operation_notes.get(key, OperationNote(key))
    play.operation_notes[key] = replace(note, observed_at=state.world_time, observed_position=(truck.x, truck.y))
    play.message = "차량 관측 위치 기록 · I 정보 노트"


def restore_notes(state, rows):
    result = {}
    roads = {frozenset((road.start, road.end)) for road in state.roads.values()}
    for row in rows:
        point = row["observed_position"]
        note = OperationNote(**{**row, "route": tuple(row["route"]),
                                "observed_position": tuple(point) if point is not None else None})
        op = state.operations[note.operation_id]
        if (note.operation_id in result or type(note.departed) is not bool
                or note.source is not None and (note.source not in state.facilities
                                                or note.source not in {op.origin_id, op.destination_id})
                or any(type(value) is not int or not 0 <= value <= state.world_time
                       for value in (note.recorded_at, note.observed_at) if value is not None)
                or note.departure_at is not None and (type(note.departure_at) is not int or note.departure_at < 0)
                or note.quantity is not None and (type(note.quantity) is not int or note.quantity < 0)
                or (note.recorded_at is None) != (note.source is None)
                or note.source is None and (note.route or note.departure_at is not None or note.departed
                                             or note.quantity is not None)
                or (note.observed_at is None) != (point is None)
                or note.recorded_at is None and note.observed_at is None
                or any(node not in state.nodes for node in note.route)
                or any(frozenset(edge) not in roads for edge in zip(note.route, note.route[1:]))
                or point is not None and (len(point) != 2 or not all(math.isfinite(axis) for axis in point))):
            raise ValueError("잘못된 작전 정보 노트")
        result[note.operation_id] = note
    return result
