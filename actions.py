"""The action rules: working out what an action would do, and doing it.

An action (configured in config.ACTIONS) can combine any of these effects,
which happen in this order:
  - tackling            ("tackle": True)  take the ball from an adjacent opponent
  - moving the player   ("self":   offset or list of offsets)
  - moving the ball     ("ball":   offset or list of offsets, only applies if
                                   the player has the ball)
  - rotating the player ("rotate": a number of clockwise turns, or "any").
                        Without "rotate", a player who moves turns to face
                        the way they moved (config.FACE_MOVEMENT).
  - shooting            ("shoot":  True)  shoot at the goal the team attacks

Offsets are measured from where the player stands and turned to the way they
face, both as they are *before* the action.

A list of offsets, or "rotate": "any", gives the player a choice: every
valid option is highlighted and they click the one they want. An action can
offer at most one choice, so a single click always settles it.

Player and ball moves can't pass through a tile with an opponent on it
(see path_blocked); "lofted": True lets an action's ball go over them.
Shots aren't blocked.

Tackles and shots can fail. A failed tackle stops the rest of the action;
a shot comes last, so nothing is left to stop.
"""
import random
from dataclasses import dataclass
from itertools import product
from typing import Optional

import config
from hex_math import direction_toward, hex_distance, neighbours, rotate_offset, tiles_between


# --- Requirements ---
# Each name usable in an action's "requires" list, and the check behind it.
# Add new conditions here.
REQUIREMENTS = {
    "possession": lambda board, tile: board.has_possession(tile),
    "no_possession": lambda board, tile: not board.has_possession(tile),
}

# Keys of config.TACKLE_CHANCE, by how many 60-degree steps the tackler is
# away from the direction the ball carrier faces (0 = right in front).
TACKLE_ANGLES = ["head on", "front side", "back side", "behind"]


@dataclass
class Outcome:
    """The result of one way of carrying out an action, if everything succeeds."""
    player_to: tuple                     # coordinate the player ends up on
    facing: int                          # direction the player faces afterwards
    ball_to: Optional[tuple]             # where the ball goes, or None if it doesn't move
    tackle_from: Optional[tuple] = None  # ball carrier's coordinate, if this tackles
    tackle_chance: float = 1.0
    shot_at: Optional[tuple] = None      # goal coordinate, if this shoots
    shot_chance: float = 1.0


@dataclass
class Result:
    """What actually happened when an Outcome was attempted."""
    tile: object                     # the Tile the acting player is on afterwards
    message: str = ""                # what to tell the player, "" for nothing
    goal_for: Optional[int] = None   # the team that scored, if a goal went in


def percent(chance):
    return f"{round(chance * 100)}%"


# --- Helpers ---

def offset_list(value):
    """An action's "self"/"ball" value as a list of offsets, whichever way it was written."""
    return value if isinstance(value, list) else [value]


def offset_coord(tile, offset):
    """The coordinate an action offset points at, turned to the player's facing."""
    dq, dr = rotate_offset(offset, tile.player.facing)
    return (tile.q + dq, tile.r + dr)


def meets_requirements(board, tile, action):
    requires = action.get("requires")
    return not requires or all(REQUIREMENTS[req](board, tile) for req in requires)


def adjacent_ball_carrier(board, tile):
    """The tile of an opponent next to this player who has the ball, or None."""
    carrier = board.get(board.ball)
    if (carrier.player is not None
            and carrier.player.team != tile.player.team
            and carrier.coord in neighbours(tile.coord)):
        return carrier
    return None


def path_blocked(board, player, start, end):
    """True if an opponent of player stands anywhere along the straight line
    from start to end. The start and end tiles themselves don't count, and
    a line that only grazes an opponent's tile still counts as blocked."""
    for coord in tiles_between(start, end):
        other = board.get(coord)
        if other is not None and other.player is not None and other.player.team != player.team:
            return True
    return False


def tackle_chance(carrier, tackler):
    """Chance of winning the ball, from where the tackler stands relative to
    the way the ball carrier is facing."""
    direction = neighbours(carrier.coord).index(tackler.coord)
    steps = (direction - carrier.player.facing) % 6
    steps = min(steps, 6 - steps)           # 0 = head on ... 3 = behind
    return config.TACKLE_CHANCE[TACKLE_ANGLES[steps]]


def shot_chance(board, team, coord):
    """Chance of scoring from coord, or None if it's too far to shoot."""
    distance = hex_distance(coord, board.goal_attacked_by(team))
    return config.SHOT_CHANCE.get(distance)


def choice_of(action):
    """Which part of an action the player chooses by clicking:
    "self", "ball", "rotate", or None for a fixed action."""
    if isinstance(action.get("self"), list):
        return "self"
    if isinstance(action.get("ball"), list):
        return "ball"
    if action.get("rotate") == "any":
        return "rotate"
    return None


# --- Working out an action ---

def outcomes(board, tile, action_name):
    """{clickable coordinate: Outcome} for the player on this tile.

    Empty if the action can't be used: a requirement fails, a tackle has no
    adjacent opponent with the ball, or no option is valid. An option is
    invalid if the player or ball would end up off the board, the player
    would land on someone, or a shot would be out of range or made without
    the ball at the player's feet.
    Doesn't check action points; that's the Game's job.
    """
    action = config.ACTIONS[action_name]
    player = tile.player
    if not meets_requirements(board, tile, action):
        return {}

    has_ball = board.has_possession(tile)
    ball_start = None               # where the ball is moved before any "ball" offset
    tackle_from, t_chance = None, 1.0
    if action.get("tackle"):
        carrier = adjacent_ball_carrier(board, tile)
        if carrier is None:
            return {}
        tackle_from, t_chance = carrier.coord, tackle_chance(carrier, tile)
        has_ball = True
        ball_start = tile.coord     # the ball comes to the tackler's tile

    # Every place the player could end up
    player_tos = [tile.coord]
    if "self" in action:
        player_tos = [coord for coord in (offset_coord(tile, o) for o in offset_list(action["self"]))
                      if coord in board and board.get(coord).player is None
                      and not path_blocked(board, player, tile.coord, coord)]

    # Every place the ball could end up (None = it doesn't move)
    ball_tos = [ball_start]
    choice = choice_of(action)
    if "ball" in action:
        if has_ball:
            lofted = action.get("lofted", False)
            ball_tos = [coord for coord in (offset_coord(tile, o) for o in offset_list(action["ball"]))
                        if coord in board
                        and (lofted or not path_blocked(board, player, tile.coord, coord))]
        elif choice == "ball":
            choice = None           # without the ball there's nothing to choose

    # Every way the player could face afterwards. An explicit "rotate" decides
    # it; otherwise moving turns them to face the way they moved.
    rotate = action.get("rotate")

    def facings(player_to):
        if rotate == "any":
            return range(6)
        if rotate is not None:
            return [(player.facing + rotate) % 6]
        if config.FACE_MOVEMENT and player_to != tile.coord:
            return [direction_toward(tile.coord, player_to, prefer=player.facing)]
        return [player.facing]

    combinations = ((player_to, ball_to, facing)
                    for player_to, ball_to in product(player_tos, ball_tos)
                    for facing in facings(player_to))
    options = {}
    for player_to, ball_to, facing in combinations:
        shot_at, s_chance = None, 1.0
        if action.get("shoot"):
            ball_at = ball_to if ball_to is not None else board.ball
            if ball_at != player_to:    # the player has to have the ball at their feet
                continue
            s_chance = shot_chance(board, player.team, player_to)
            if s_chance is None:
                continue
            shot_at = board.goal_attacked_by(player.team)

        outcome = Outcome(player_to, facing, ball_to, tackle_from, t_chance, shot_at, s_chance)
        if not changes_something(tile, outcome):
            continue
        target = click_target(board, tile, outcome, choice)
        if target is not None:
            options[target] = outcome
    return options


def changes_something(tile, outcome):
    return (outcome.player_to != tile.coord
            or outcome.facing != tile.player.facing
            or outcome.ball_to is not None
            or outcome.shot_at is not None)


def click_target(board, tile, outcome, choice):
    """The tile the player clicks to pick this outcome, or None if it can't be clicked."""
    if choice == "self":
        return outcome.player_to                    # where the player goes
    if choice == "ball":
        return outcome.ball_to                      # where the ball goes
    faced = neighbours(outcome.player_to)[outcome.facing]
    if choice == "rotate":
        return faced if faced in board else None    # the tile they'll face

    # Fixed action: one tile to click to confirm it
    if outcome.tackle_from is not None:
        return outcome.tackle_from                  # the player being tackled
    if outcome.player_to != tile.coord:
        return outcome.player_to
    if outcome.shot_at is not None:
        return outcome.shot_at                      # the goal
    if outcome.ball_to is not None:
        return outcome.ball_to
    return faced if faced in board else tile.coord


# --- Carrying out an action ---

def apply_outcome(board, tile, outcome, roll=random.random):
    """Attempt an Outcome, rolling for any tackle or shot.

    roll() returns a number in [0, 1); something with a 0.6 chance works
    when roll() < 0.6. Pass your own roll to make results predictable.
    """
    messages = []

    if outcome.tackle_from is not None:
        if roll() >= outcome.tackle_chance:
            return Result(tile, f"Tackle failed ({percent(outcome.tackle_chance)})")
        messages.append(f"Tackle won ({percent(outcome.tackle_chance)})")

    if outcome.ball_to is not None:
        board.ball = outcome.ball_to
    destination = board.get(outcome.player_to)
    if destination is not tile:
        board.move_player(tile, destination)
    destination.player.facing = outcome.facing

    if outcome.shot_at is not None:
        board.ball = outcome.shot_at    # in the net, or loose on the goal tile if missed
        if roll() < outcome.shot_chance:
            messages.append(f"Goal! ({percent(outcome.shot_chance)})")
            return Result(destination, " - ".join(messages), goal_for=destination.player.team)
        messages.append(f"Shot missed ({percent(outcome.shot_chance)})")

    return Result(destination, " - ".join(messages))


# --- Config checking ---

ACTION_KEYS = {"cost", "requires", "tackle", "self", "ball", "lofted", "rotate", "shoot"}
EFFECT_KEYS = ("tackle", "self", "ball", "rotate", "shoot")


def validate_config():
    """Raise ValueError with a clear message if the action config or setup is wrong."""
    def is_offset(value):
        return (isinstance(value, tuple) and len(value) == 2
                and all(isinstance(n, int) for n in value))

    def is_offsets(value):
        if isinstance(value, list):
            return len(value) > 0 and all(is_offset(o) for o in value)
        return is_offset(value)

    def is_chance(value):
        return isinstance(value, (int, float)) and 0 <= value <= 1

    for name, action in config.ACTIONS.items():
        where = f"config.ACTIONS[{name!r}]"
        unknown = set(action) - ACTION_KEYS
        if unknown:
            raise ValueError(f"{where} has unknown keys {sorted(unknown)}; "
                             f"allowed: {sorted(ACTION_KEYS)}")
        if not isinstance(action.get("cost"), int) or action["cost"] < 0:
            raise ValueError(f"{where} needs a 'cost' of 0 or more")
        for req in action.get("requires", []):
            if req not in REQUIREMENTS:
                raise ValueError(f"{where} requires unknown {req!r}; "
                                 f"known: {sorted(REQUIREMENTS)}")
        for key in ("self", "ball"):
            if key in action and not is_offsets(action[key]):
                raise ValueError(f"{where}[{key!r}] must be an offset like (1, 0) "
                                 f"or a list of them like [(1, 0), (0, 1)]")
        rotate = action.get("rotate")
        if rotate is not None and rotate != "any" and not isinstance(rotate, int):
            raise ValueError(f"{where}['rotate'] must be a number of turns or 'any'")
        choices = [key for key in ("self", "ball") if isinstance(action.get(key), list)]
        if rotate == "any":
            choices.append("rotate")
        if len(choices) > 1:
            raise ValueError(f"{where} offers more than one choice ({', '.join(choices)}); "
                             f"only one of 'self' list, 'ball' list or 'rotate': 'any' is allowed")
        for key in ("tackle", "shoot", "lofted"):
            if key in action and not isinstance(action[key], bool):
                raise ValueError(f"{where}[{key!r}] must be True or False")
        if not any(action.get(key) for key in EFFECT_KEYS):
            raise ValueError(f"{where} doesn't do anything: give it one of {list(EFFECT_KEYS)}")

    if set(config.TACKLE_CHANCE) != set(TACKLE_ANGLES):
        raise ValueError(f"config.TACKLE_CHANCE needs exactly the keys {TACKLE_ANGLES}")
    for table in ("TACKLE_CHANCE", "SHOT_CHANCE"):
        for key, value in getattr(config, table).items():
            if not is_chance(value):
                raise ValueError(f"config.{table}[{key!r}] must be a chance from 0 to 1")

    for team, controller in config.CONTROLLERS.items():
        if controller not in ("human", "ai"):
            raise ValueError(f"config.CONTROLLERS[{team!r}] must be 'human' or 'ai'")
    weight_keys = {"goal", "possession", "loose_ball", "progress", "shot_chance",
                   "ball_safety", "support", "cover"}
    if set(config.AI_WEIGHTS) != weight_keys:
        raise ValueError(f"config.AI_WEIGHTS needs exactly the keys {sorted(weight_keys)}")

    used = list(config.DEFAULT_ACTIONS)
    for entries in config.INITIAL_SETUP.values():
        for entry in entries:
            used += entry["actions"]
    for name in used:
        if name not in config.ACTIONS:
            raise ValueError(f"Action {name!r} is given to a player but isn't in config.ACTIONS")
