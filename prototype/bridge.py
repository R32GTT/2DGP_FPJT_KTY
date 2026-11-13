"""기존 군수 코어의 시간·창고 회계를 연결하는 좁은 어댑터."""

from campaign.scenario import load_scenario
from campaign.simulation import Simulation


class CampaignBridge:
    def __init__(self, facility_id="depot_a"):
        self.simulation = Simulation(load_scenario())
        self.simulation.advance_to(0)
        self.facility_id = facility_id

    @property
    def supplies(self):
        return self.simulation.state.facilities[self.facility_id].inventory["supplies"]

    def advance_to(self, seconds):
        target = int(seconds + 1e-8)
        if target > self.simulation.state.world_time:
            self.simulation.advance_to(target)

    def take_supplies(self):
        amount = min(10, self.supplies)
        if amount:
            self.simulation.apply_action({"type": "capture", "owner_id": self.facility_id,
                                          "amount": {"supplies": amount}})
        return amount
