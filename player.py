from dataclasses import dataclass


@dataclass
class Player:
    team: int        # config.HOME or config.AWAY
    actions: list    # names of actions in config.ACTIONS, e.g. ["reposition", "pass"]
    facing: int = 0  # index into hex_math.DIRECTIONS (0 = east, then clockwise)
