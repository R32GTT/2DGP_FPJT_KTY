from campaign.models import Operation


def make_operation(state, *, vehicle="a_truck_1", unit="a_escort_1",
                   origin="depot_a", destination="hq_b", kind="supply"):
    source = state.source if origin == state.source.id else state.facilities[origin]
    node = (state.demand.node_id if kind == "export"
            else state.facilities[destination].node_id)
    return Operation(state.new_id(), "검증용 실제 수요", kind, origin, destination,
                     source.node_id, node, [vehicle], [unit], state.world_time)
