from dataclasses import dataclass


@dataclass
class Player:
    team: int        # config.HOME or config.AWAY
    actions: list    # names of actions in config.ACTIONS, e.g. ["reposition", "pass"]
    facing: int = 0  # hex_math.RIGHT or hex_math.LEFT
    role: str = "outfield"   # "outfield" or "goalkeeper"

    @property
    def is_keeper(self):
        return self.role == "goalkeeper"
