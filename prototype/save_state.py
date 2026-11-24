"""현장의 지속 상태만 변환한다. 경로·그리기·행동 트리 캐시는 재생성한다."""

from dataclasses import asdict
import math

from campaign.models import Position
from .actors import Actor
from .ai_state import Brain, CombatState, Incident, RadioState, SearchCue
from .lighting import OPTICS_MODES
from .perception import Knowledge
from .simulation import RegionAlarm
from .transport import Trip
from .weapons import WeaponState


RUNTIME = ("time", "footstep_at", "respawn_target", "kit_available")
SEARCH = ("next_sweep_at", "sweep_pass", "look_until", "checked_sound_at")


def tuples(value):
    return tuple(map(tuples, value)) if isinstance(value, list) else value


def weapons(loadout):
    return {key: {name: value for name, value in vars(weapon).items() if name != "spec"}
            for key, weapon in loadout.items()}


def restore_weapons(rows, specs):
    return {key: WeaponState(specs[key], **row) for key, row in rows.items()}


def actor_state(actor):
    return {**vars(actor), "position": asdict(actor.position), "loadout": weapons(actor.loadout)}


def restore_actor(row, specs):
    actor = Actor(**{**row, "position": Position(**row["position"]),
                     "loadout": restore_weapons(row["loadout"], specs),
                     "boarding_goal": tuples(row["boarding_goal"])})
    if (not all(math.isfinite(value) for value in (*actor.xy, actor.hp, actor.facing, actor.radius,
                                                 actor.speed, actor.blinded_until))
            or actor.hp < 0 or actor.radius <= 0 or actor.speed <= 0
            or actor.blinded_until < 0
            or actor.lamp not in OPTICS_MODES or type(actor.night_vision) is not bool or type(actor.ir_laser) is not bool
            or actor.active_weapon and actor.active_weapon not in actor.loadout):
        raise ValueError("잘못된 현장 개체 상태")
    return actor


def capture(play):
    roster = play.roster
    brains = {}
    for key, brain in play.ai.brains.items():
        brains[key] = {"patrol": brain.patrol, "waypoint": brain.waypoint, "incident": brain.incident,
                       "radio_at": brain.radio_at, "contact_at": brain.contact_at,
                       "resupply_at": brain.resupply_at,
                       "radio": asdict(brain.radio),
                       "memory": asdict(brain.memory), "combat": asdict(brain.combat),
                       "search": {key: getattr(brain.search, key) for key in SEARCH},
                       "checked": list(brain.search.checked),
                       "priority": asdict(brain.search.priority_cue) if brain.search.priority_cue else None}
    return {"runtime": {key: getattr(play, key) for key in RUNTIME}, "player": actor_state(play.player),
            "respawn_site_id": play.respawn_site_id,
            "alarms": {key: asdict(alarm) for key, alarm in play.alarms.items() if alarm.until > play.time},
            "actors": [actor_state(actor) for actor in roster.actors], "brains": brains,
            "roster": {"spawns": roster.spawns, "deaths": sorted(roster.local_deaths),
                       "seated_deaths": sorted(roster.seated_deaths), "materialized": roster.materialized},
            "trips": {key: {"seats": {vid: [a.id for a in crew] for vid, crew in trip.seats.items()},
                            "exits": trip.exits, "stopped_at": trip.stopped_at}
                      for key, trip in play.transports.trips.items()},
            "dropped": [[point, weapons(loadout)] for point, loadout in play.dropped],
            "safehouse_stash": {key: weapons({weapon.spec.id: weapon})
                                for key, weapon in play.safehouse_stash.items()},
            "inventory": {"carried": play.inventory.carried, "stored": play.inventory.stored,
                          "ground": play.inventory.ground, "selected_throwable": play.inventory.selected_throwable},
            "throwables": play.throwables.snapshot(),
            "random": play.combat.random.getstate(), "reports": play.campaign._reports,
            "resumed_at": play.campaign._resumed_at,
            "received": sorted(play.presence.received)}


def restore(play, row):
    state, roster = play.campaign.simulation.state, play.roster
    if set(row["runtime"]) != set(RUNTIME):
        raise ValueError("현장 저장 필드 불일치")
    for key in RUNTIME:
        setattr(play, key, row["runtime"][key])
    if not math.isfinite(play.time) or int(play.time + 1e-8) != state.world_time:
        raise ValueError("현장·캠페인 시각 불일치")
    for key, saved in row.get("alarms", {}).items():
        position, until = tuple(saved["position"]), saved["until"]
        if (key not in state.regions or len(position) != 2 or not all(map(math.isfinite, position))
                or not math.isfinite(until) or until <= play.time):
            raise ValueError("잘못된 지역 경보 상태")
        play.alarms[key] = RegionAlarm(position, until)
    play.player = restore_actor(row["player"], play.specs)
    play.respawn_site_id = row.get("respawn_site_id")
    if play.respawn_site_id is not None and (play.respawn_target is None or play.respawn_site_id not in play.respawn_sites):
        raise ValueError("잘못된 안전가옥 복귀 지점")
    play.world.update_chunks(play.player.xy)
    roster.actors[:] = [restore_actor(actor, play.specs) for actor in row["actors"]]
    roster.by_id = {actor.id: actor for actor in roster.actors}
    if len(roster.by_id) != len(roster.actors) or set(row["brains"]) != set(roster.by_id):
        raise ValueError("중복 병사 또는 AI 상태 누락")
    for actor in roster.actors:
        if (actor.source_unit not in state.units or type(actor.member) is not int or actor.member < 0
                or actor.id != f"{actor.source_unit}:{actor.member}" or actor.home_node not in state.nodes):
            raise ValueError("캠페인 병사 참조 불일치")
    roster.unit_ids = sorted({actor.source_unit for actor in roster.actors})
    roster.spawns = {key: tuple(point) for key, point in row["roster"]["spawns"].items()}
    roster.local_deaths, roster.seated_deaths = (set(row["roster"][key]) for key in ("deaths", "seated_deaths"))
    if (set(roster.spawns) != set(roster.by_id) or not roster.seated_deaths <= roster.local_deaths <= roster.by_id.keys()
            or any(roster.by_id[key].hp > 0 for key in roster.local_deaths)):
        raise ValueError("병사 손실·초기 위치 불일치")
    play.ai.brains.clear()
    for key, saved in row["brains"].items():
        brain = Brain([tuple(point) for point in saved["patrol"]])
        brain.waypoint, brain.incident = saved["waypoint"], saved["incident"]
        brain.radio_at, brain.contact_at, brain.sense_at = saved["radio_at"], saved["contact_at"], play.time
        brain.resupply_at = saved.get("resupply_at", 0.0)
        if type(brain.resupply_at) not in {float, int} or not math.isfinite(brain.resupply_at) or brain.resupply_at < 0:
            raise ValueError("잘못된 탄약 보급 재시도 시각")
        brain.radio = RadioState(**{key: tuples(value) for key, value in saved.get("radio", {}).items()})
        brain.memory = Knowledge(**{key: tuples(value) for key, value in saved["memory"].items()})
        started = brain.radio.started_at
        if started is not None and (not math.isfinite(started) or started > play.time
                                    or brain.contact_at is None or brain.memory.last_seen is None):
            raise ValueError("잘못된 무전 진행 상태")
        brain.combat = CombatState(**{key: tuples(value) for key, value in saved["combat"].items()})
        for name in SEARCH:
            setattr(brain.search, name, saved["search"][name])
        brain.search.checked = set(map(tuples, saved["checked"]))
        if saved["priority"]:
            brain.search.priority_cue = SearchCue(**{**saved["priority"], "point": tuple(saved["priority"]["point"])})
        play.ai.brains[key] = brain
        point = brain.memory.last_seen or brain.memory.reported
        if brain.incident and point is not None:
            play.ai.incidents.setdefault(brain.incident, Incident(play.time, point))
    play.transports.trips = {key: Trip({vid: [roster.by_id[aid] for aid in crew] for vid, crew in trip["seats"].items()},
                                      {aid: tuple(point) for aid, point in trip["exits"].items()}, trip["stopped_at"])
                             for key, trip in row["trips"].items()}
    play.dropped = [(tuple(point), restore_weapons(loadout, play.specs)) for point, loadout in row["dropped"]]
    for key, slot in row.get("safehouse_stash", {}).items():
        if key not in play.respawn_sites or len(slot) != 1:
            raise ValueError("잘못된 안전가옥 보관함")
        play.safehouse_stash[key] = next(iter(restore_weapons(slot, play.specs).values()))
    inventory = row["inventory"]
    play.inventory.carried = play.inventory.validate_items(inventory["carried"])
    selection = inventory["selected_throwable"]
    if selection is not None and (selection not in play.inventory.carried or not selection.startswith("throwable:")):
        raise ValueError("잘못된 투척물 선택 상태")
    play.inventory.selected_throwable = selection
    play.throwables.restore(row["throwables"], play.time)
    play.inventory.stored = {key: play.inventory.validate_items(items) for key, items in inventory["stored"].items()}
    if not play.inventory.stored.keys() <= play.respawn_sites.keys():
        raise ValueError("잘못된 인벤토리 보관소")
    play.inventory.ground = []
    for point, items in inventory["ground"]:
        if len(point) != 2 or not all(math.isfinite(value) for value in point):
            raise ValueError("잘못된 현장 물자 위치")
        play.inventory.ground.append((tuple(point), play.inventory.validate_items(items)))
    play.combat.random.setstate(tuples(row["random"]))
    play.campaign._reports, play.campaign._resumed_at = row["reports"], row["resumed_at"]
    play.presence.received = set(map(tuple, row["received"]))
    roster.materialized = row["roster"]["materialized"]
    play._sync_guards()
