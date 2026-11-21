"""The action rules: working out what an action would do, and doing it.

An action (configured in config.ACTIONS) can combine any of these effects,
which happen in this order:
  - moving the player   ("self":   offset or list of offsets)
  - moving the ball     ("ball":   offset or list of offsets, or "with_player"
                                   to go wherever the player moves; only
                                   applies if the player has the ball)
  - turning round      ("turn": True)  flip the player to face the other way,
                        after any movement; or ("face_movement": True) face
                        the side they moved towards, like the free step.
                        Actions never change a player's facing otherwise.
  - shooting            ("shoot":  True)  shoot at the goal the team attacks
  - shielding           ("shield": amount)  tackles on the player by
                        opponents moving across them have this much less
                        chance, e.g. 0.3 takes 80% down to 50%, until the
                        team's next turn starts or the player moves

Offsets are written for a player facing right, measured from where the
player stands before the action. For a player facing left they're mirrored
left-to-right, so up and down on screen stay the same.

A list of offsets gives the player a choice: every valid option is
highlighted and they click the one they want. An action can offer at most one
choice (a "self" list or a "ball" list), so a single click always settles it.

Player and ball moves can't pass through a tile with an opponent on it
(see path_blocked); "lofted": True lets an action's ball go over them. With
"ball": "with_player", "lofted" lets the player (carrying the ball) go over
them too.
Shots aren't blocked.

Tackling: there's no tackle action. A player tackles by moving across the
opponent who has the ball: from a tile next to the carrier to another tile
next to them (see crossing; a single step round them counts). The free step
and any action that moves a player can do it. The chance is TACKLE_CHANCE
for the side of the carrier the tackler starts from (front or behind, by the
way the carrier faces), less the carrier's shield. Either way the tackler
ends up where they were going: with the ball if the tackle wins.

Going past: a carrier who moves past an opponent the same way (from a tile
next to them to another tile next to them) gives that opponent a free
tackle, with TACKLE_CHANCE for the side of the carrier the opponent is on.
Shields don't count (moving ends them anyway). Each opponent passed gets one,
in the order the move passes them; if one wins, the ball goes to them and the
carrier still finishes the move, without it. A lofted carry (hurdle)
escapes. Moving straight away from an opponent is safe.

Long moves: with TACKLE_MOVE_CHECK = "path", every step along a move's
straight line counts, so a two-tile run can cross or pass someone on the way;
with "ends", only where the move starts and finishes.

Interceptions: a pass that isn't lofted can be intercepted by any opponent
next to a tile it crosses (not counting the start and destination tiles),
with INTERCEPT_CHANCE for each such tile, in order along the path. The first
success sends the ball to that opponent instead.
"""
import random
from dataclasses import dataclass
from itertools import product
from typing import Optional

import config
from hex_math import (facing_after_move, flipped, hex_distance, is_in_front, neighbours,
                      offset_for_facing, rectangle_coords, tiles_between)


# --- Requirements ---
# Each name usable in an action's "requires" list, and the check behind it.
# Add new conditions here.
# Settings added after config.py files already existed. If a config.py
# doesn't have one, it gets this value, which plays the way the game did
# before the setting existed. (The menu's Save to config.py writes them in.)
def config_defaults():
    return {
        "ACTIONS_PER_TURN": {team: 1 for team in config.INITIAL_SETUP},
        "START_POSSESSION_ACTIONS": 1,
        "TACKLE_POSSESSION_ACTIONS": 1,
        "RECEIVE_POSSESSION_ACTIONS": 1,
        "TURNS_PER_TEAM": 0,
        "AI_RESULT_WEIGHT": 2.0,
        "AI_CHASE_BOOST": {"behind": 0.0, "level": 0.0, "ahead": 0.0},   # 0 = ignore the score
        "RANKING_TEAMS": 100, "RANKING_TOP_RATING": 2000, "RANKING_POINTS_PER_PLACE": 10,
        "RANKING_WIN_SHARE": 0.5, "RANKING_MIN_WIN": 10, "RANKING_DRAW_SHARE": 0.1,
        "RANKING_LOSS_MAX": 50, "RANKING_LOSS_HALF_GAP": 300,
        "TACKLE_MOVE_CHECK": "path",        # long moves tackle along every step ("ends": start/end only)
        "TEXT_COLOUR": (0, 0, 0), "FAINT_TEXT_COLOUR": (140, 140, 140), "ERROR_COLOUR": (190, 0, 0),
        "INPUT_COLOUR": (255, 255, 255), "STRIPE_COLOUR": (228, 228, 228),
        "HIGHLIGHT_ROW_COLOUR": (255, 236, 160),
        "DARK_MODE": False, "DARK_MODE_COLOURS": {}, "FULLSCREEN": False,
        "AI_PROGRESS_STOP": None, "REPLAY_FOLDER": "replays",
        "INTERCEPT_CHANCE": 0.0,            # 0 = no interceptions
        "MAX_PLAYER_ACTIONS": 0,            # 0 = no limit
        "SHIFTED_ROWS": "second",           # which rows sit half a tile across
        "PREVIEW_TARGET_COLOUR": (215, 215, 215),   # border on tiles a hovered action could reach
        "SHIELD_COLOUR": (255, 255, 255),   # outline on a shielding player's tile
        "SHIELD_WIDTH": 2,
        "SHIELD_INSET": 5,
    }


# AI evaluation weights added after config.py files already had AI_WEIGHTS.
# A config without one gets this value (the AI reads it with ai.weight()).
AI_WEIGHT_DEFAULTS = {
    "trapped": 0.25,
}
REQUIRED_AI_WEIGHTS = {"goal", "possession", "loose_ball", "progress", "shot_chance",
                       "ball_safety", "shot_threat", "support", "cover", "pressing", "runs"}


def apply_config_defaults():
    """Give config any setting it's missing (see config_defaults)."""
    for name, value in config_defaults().items():
        if not hasattr(config, name):
            setattr(config, name, value)


apply_config_defaults()


REQUIREMENTS = {
    "possession": lambda board, tile: board.has_possession(tile),
    "no_possession": lambda board, tile: not board.has_possession(tile),
    "goalkeeper": lambda board, tile: tile.player.is_keeper,
    "in_own_box": lambda board, tile: board.in_box(tile.coord, tile.player.team),
}

# "ball": WITH_PLAYER means the ball ends up wherever the player moves to,
# so a "self" list is one decision for both (a dribble in a chosen direction).
WITH_PLAYER = "with_player"

# Keys of config.TACKLE_CHANCE: is the tackler on the side the ball carrier
# is facing, or behind them?
TACKLE_ANGLES = ["front", "behind"]


@dataclass
class Outcome:
    """The result of one way of carrying out an action, if everything succeeds."""
    player_to: tuple                     # coordinate the player ends up on
    facing: int                          # direction the player faces afterwards
    ball_to: Optional[tuple]             # where the ball goes, or None if it doesn't move
    tackle_from: Optional[tuple] = None  # ball carrier's coordinate, if the move goes across them
    tackle_chance: float = 1.0
    shot_at: Optional[tuple] = None      # goal coordinate, if this shoots
    shot_chance: float = 1.0             # chance the shot is on target (beats everything but the keeper)
    save_chance: float = 0.0             # chance the defending keeper saves an on-target shot
    keeper_at: Optional[tuple] = None    # where that keeper stands (they catch a save)
    carry_tackles: tuple = ()            # ((opponent coord, chance), ...) the carrier goes past, in order
    intercepts: tuple = ()               # ((opponent coord, chance), ...) along the pass, in order
    shield: float = 0.0                  # the player's shield afterwards

    @property
    def score_chance(self):
        """Chance the shot goes in: on target and not saved."""
        return self.shot_chance * (1 - self.save_chance)

    @property
    def loss_chance(self):
        """Chance an opponent the carrier goes past, or an interception, takes the ball."""
        keep = 1.0
        for _, chance in self.carry_tackles + self.intercepts:
            keep *= 1 - chance
        return 1 - keep


@dataclass
class Result:
    """What actually happened when an Outcome was attempted."""
    tile: object                     # the Tile the acting player is on afterwards
    message: str = ""                # what to tell the player, "" for nothing
    goal_for: Optional[int] = None   # the team that scored, if a goal went in
    tackle_won: bool = False         # the player won the ball by moving across the carrier
    lost_ball: bool = False          # an opponent they went past, or an interception, took it


def percent(chance):
    return f"{round(chance * 100)}%"


# --- Helpers ---

def offset_list(value):
    """An action's "self"/"ball" value as a list of offsets, whichever way it
    was written. Empty for "with_player", which isn't an offset."""
    if value == WITH_PLAYER:
        return []
    return value if isinstance(value, list) else [value]


def offset_coord(tile, offset):
    """The coordinate an action offset points at, for the way the player faces."""
    dq, dr = offset_for_facing(offset, tile.player.facing)
    return (tile.q + dq, tile.r + dr)


def player_actions(own):
    """Every action a player with this "actions" list ends up with: their own,
    then config.DEFAULT_ACTIONS they don't already have (as board.py does)."""
    own = list(own)
    return own + [name for name in config.DEFAULT_ACTIONS if name not in own]


def meets_requirements(board, tile, action):
    requires = action.get("requires")
    return not requires or all(REQUIREMENTS[req](board, tile) for req in requires)



def path_blocked(board, player, start, end):
    """True if an opponent of player stands anywhere along the straight line
    from start to end. The start and end tiles themselves don't count, and
    a line that only grazes an opponent's tile still counts as blocked."""
    for coord in tiles_between(start, end):
        other = board.get(coord)
        if other is not None and other.player is not None and other.player.team != player.team:
            return True
    return False


def shielded(chance, player):
    """A tackle chance against player, less their shield (never below 0)."""
    return max(0.0, chance - player.shield)


def tackle_side(carrier_coord, facing, other):
    """"front" if the tile `other` (next to carrier_coord) is on the side a
    carrier there facing `facing` faces, else "behind"."""
    direction = neighbours(carrier_coord).index(other)
    return "front" if is_in_front(facing, direction) else "behind"


def tackle_chance(carrier, tackler):
    """Chance a tackler next to the carrier (both Tiles) wins the ball by
    moving across them from there: TACKLE_CHANCE for the side they start on,
    less the carrier's shield."""
    side = tackle_side(carrier.coord, carrier.player.facing, tackler.coord)
    return shielded(config.TACKLE_CHANCE[side], carrier.player)


def move_tiles(start, end):
    """The tiles a move from start to end goes through, start and end
    included, in order along its straight line (two tiles the line runs
    between both count, at the same point)."""
    between = sorted(tiles_between(start, end), key=lambda c: (hex_distance(start, c), c))
    return [start] + between + [end]


def crossing(start, end, around):
    """Where a move from start to end goes across (or past) the tile `around`:
    the tile of the move it starts doing so from, or None if it doesn't.

    It does if the move goes from a tile next to `around` to another tile
    next to it, a single step round it included. With
    config.TACKLE_MOVE_CHECK "ends" those are the move's start and end; with
    "path" they're any two neighbouring tiles along the way."""
    if start == end or around in (start, end):
        return None
    near = set(neighbours(around))
    if config.TACKLE_MOVE_CHECK == "ends":
        return start if start in near and end in near else None
    touching = [c for c in move_tiles(start, end) if c in near]
    for i, a in enumerate(touching):
        if any(hex_distance(a, b) == 1 for b in touching[i + 1:]):
            return a
    return None


def moving_tackle(board, tile, player_to):
    """(carrier coord, chance) if the player on tile, moving to player_to,
    goes across an opponent who has the ball; None if not or if the chance
    is 0. The chance is TACKLE_CHANCE for the side of the carrier the move
    starts crossing from, less the carrier's shield."""
    carrier = board.get(board.ball)
    if (carrier.player is None or carrier is tile or player_to == tile.coord
            or carrier.player.team == tile.player.team):
        return None
    start = crossing(tile.coord, player_to, carrier.coord)
    if start is None:
        return None
    side = tackle_side(carrier.coord, carrier.player.facing, start)
    chance = shielded(config.TACKLE_CHANCE[side], carrier.player)
    return (carrier.coord, chance) if chance > 0 else None


def carry_tackles(board, tile, player_to):
    """((opponent coord, chance), ...) for the opponents a ball carrier on
    tile goes past moving (with the ball) to player_to: those the move goes
    from one tile next to to another (see crossing). In the order the move
    passes them, best chance first when level. The chance is TACKLE_CHANCE
    for the side of the carrier (facing as before the move) the opponent is
    on where the move reaches them; shields don't count. 0% ones left out."""
    player = tile.player
    path = move_tiles(tile.coord, player_to)
    order = {coord: i for i, coord in enumerate(path)}
    nearby = {n for coord in path for n in neighbours(coord)} - set(path)
    found = []
    for coord in nearby:
        other = board.get(coord)
        if other is None or other.player is None or other.player.team == player.team:
            continue
        at = crossing(tile.coord, player_to, coord)
        if at is None:
            continue
        chance = config.TACKLE_CHANCE[tackle_side(at, player.facing, coord)]
        if chance > 0:
            found.append((order[at], -chance, coord, chance))
    return tuple((coord, chance) for _, _, coord, chance in sorted(found))


def interceptions(board, team, start, end):
    """((opponent coord, chance), ...) for a pass from start to end: one roll
    of INTERCEPT_CHANCE for each tile the ball crosses (start and end not
    counted) that each opponent stands next to, in order along the path."""
    chance = config.INTERCEPT_CHANCE
    if chance <= 0:
        return ()
    rolls = []
    for coord in sorted(tiles_between(start, end), key=lambda c: (hex_distance(start, c), c)):
        for n in sorted(neighbours(coord)):
            tile = board.get(n)
            if (tile is not None and tile.player is not None and tile.player.team != team
                    and n not in (start, end)):
                rolls.append((n, chance))
    return tuple(rolls)


def keeper_save_chance(board, defending_team):
    """Chance the defending team's keeper saves an on-target shot, from
    KEEPER_SAVE_CHANCE by their distance from goal. 0 with no keeper, or a
    keeper outside their box (saves are a goalkeeping action)."""
    keeper = board.keeper_tile(defending_team)
    if keeper is None or not board.in_box(keeper.coord, defending_team):
        return 0.0
    return config.KEEPER_SAVE_CHANCE.get(hex_distance(keeper.coord, board.own_goal(defending_team)), 0.0)


def scoring_chance(board, team, coord):
    """Chance a shot from coord actually goes in: on target and not saved.
    0 if it's too far to shoot."""
    chance = shot_chance(board, team, coord) or 0.0
    defending = config.AWAY if team == config.HOME else config.HOME
    return chance * (1 - keeper_save_chance(board, defending))


def shot_chance(board, team, coord):
    """Chance a shot from coord is on target (before the keeper), or None if
    it's too far to shoot."""
    distance = hex_distance(coord, board.goal_attacked_by(team))
    return config.SHOT_CHANCE.get(distance)


def choice_of(action):
    """Which part of an action the player chooses by clicking:
    "self", "ball", or None for a fixed action."""
    if isinstance(action.get("self"), list):
        return "self"
    if isinstance(action.get("ball"), list):
        return "ball"
    return None


# --- Working out an action ---

def preview_outcomes(board, tile, action_name):
    """What the action would do from here if the player could use it now, for
    showing an action they can't use: its requirements are ignored, and an
    action that moves or shoots the ball (or needs it) is worked out as if the
    player had the ball at their feet. Same format as outcomes()."""
    action = config.ACTIONS[action_name]
    uses_ball = ("ball" in action or action.get("shoot")
                 or "possession" in action.get("requires", []))
    saved = board.ball
    if uses_ball:
        board.ball = tile.coord
    try:
        return outcomes(board, tile, action_name, check_requirements=False)
    finally:
        board.ball = saved


def outcomes(board, tile, action_name, check_requirements=True):
    """{clickable coordinate: Outcome} for the player on this tile.

    Empty if the action can't be used: a requirement fails, or no option is
    valid. An option is
    invalid if the player or ball would end up off the board, the player
    would land on someone, or a shot would be out of range or made without
    the ball at the player's feet.
    Doesn't check whether the team has an action left; that's the Game's job.
    """
    action = config.ACTIONS[action_name]
    player = tile.player
    if check_requirements and not meets_requirements(board, tile, action):
        return {}

    has_ball = board.has_possession(tile)
    ball_start = None               # where the ball is moved before any "ball" offset

    follows = action.get("ball") == WITH_PLAYER and has_ball   # ball goes where the player goes
    # A lofted ball carried by the player: player and ball go over opponents
    hurdles = follows and action.get("lofted", False)

    # Every place the player could end up
    player_tos = [tile.coord]
    if "self" in action:
        player_tos = [coord for coord in (offset_coord(tile, o) for o in offset_list(action["self"]))
                      if coord in board and board.get(coord).player is None
                      and (hurdles or not path_blocked(board, player, tile.coord, coord))]

    # Every place the ball could end up (None = it doesn't move)
    ball_tos = [ball_start]
    choice = choice_of(action)
    if "ball" in action and not follows:
        if has_ball:
            lofted = action.get("lofted", False)
            ball_tos = [coord for coord in (offset_coord(tile, o) for o in offset_list(action["ball"]))
                        if coord in board
                        and (lofted or not path_blocked(board, player, tile.coord, coord))]
        elif choice == "ball":
            choice = None           # without the ball there's nothing to choose

    # Actions keep the player's facing unless they say to turn round, or to
    # face the way they moved
    def facing_at(player_to):
        if action.get("turn"):
            return flipped(player.facing)
        if action.get("face_movement"):
            return facing_after_move(tile.coord, player_to, player.facing)
        return player.facing

    options = {}
    for player_to, ball_to in product(player_tos, ball_tos):
        facing = facing_at(player_to)
        if follows:
            ball_to = player_to     # its path is the player's, already checked for blockers
        shot_at, s_chance = None, 1.0
        if action.get("shoot"):
            ball_at = ball_to if ball_to is not None else board.ball
            if ball_at != player_to:    # the player has to have the ball at their feet
                continue
            s_chance = shot_chance(board, player.team, player_to)
            if s_chance is None:
                continue
            shot_at = board.goal_attacked_by(player.team)
        save, keeper_at = 0.0, None
        if shot_at is not None:
            defending = config.AWAY if player.team == config.HOME else config.HOME
            save = keeper_save_chance(board, defending)
            keeper_at = board.keeper_tile(defending).coord if save > 0 else None

        carried, intercepts = (), ()
        if follows and player_to != tile.coord and not hurdles:
            # Carrying the ball past opponents
            carried = carry_tackles(board, tile, player_to)
        elif ball_to is not None and ball_to != tile.coord and not action.get("lofted", False):
            intercepts = interceptions(board, player.team, tile.coord, ball_to)
        tackle_from, t_chance = None, 1.0
        if not has_ball:
            tackle = moving_tackle(board, tile, player_to)   # moving across the carrier
            if tackle is not None:
                tackle_from, t_chance = tackle

        # Moving ends a shield; a shielding action sets one after any movement
        shield = 0.0 if player_to != tile.coord else player.shield
        if action.get("shield"):
            shield = max(shield, action["shield"])

        outcome = Outcome(player_to, facing, ball_to, tackle_from, t_chance, shot_at, s_chance,
                          save, keeper_at, carried, intercepts, shield)
        if not changes_something(tile, outcome):
            if outcome.shield <= player.shield:
                continue
            target = tile.coord         # only shields: click the player themselves
        else:
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

    # Fixed action: one tile to click to confirm it
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

    # Moving across the carrier: the player goes where they were going either way
    tackle_won = False
    if outcome.tackle_from is not None:
        tackle_won = roll() < outcome.tackle_chance
        messages.append(f"Tackle {'won' if tackle_won else 'failed'} "
                        f"({percent(outcome.tackle_chance)})")

    # Carrying the ball past opponents: each gets a tackle, in order
    lost = False
    for coord, chance in outcome.carry_tackles:
        if roll() < chance:
            board.ball = coord
            lost = True
            messages.append(f"Tackled going past ({percent(chance)})")
            break
    if outcome.carry_tackles and not lost:
        messages.append("Got past the tackle" + ("s" if len(outcome.carry_tackles) > 1 else ""))

    intercepted = False
    if outcome.ball_to is not None and not lost:
        board.ball = outcome.ball_to
        for coord, chance in outcome.intercepts:
            if roll() < chance:
                board.ball = coord
                intercepted = True
                messages.append(f"Intercepted ({percent(chance)})")
                break
    destination = board.get(outcome.player_to)
    old_shield = tile.player.shield
    if destination is not tile:
        board.move_player(tile, destination)
    if tackle_won:
        board.ball = destination.coord
    destination.player.facing = outcome.facing
    destination.player.shield = outcome.shield
    if outcome.shield > old_shield or (outcome.shield and destination is not tile):
        messages.append(f"Shielding: -{round(outcome.shield * 100)} points on tackles")

    if outcome.shot_at is not None and not intercepted and not lost:
        if roll() >= outcome.shot_chance:
            board.ball = outcome.shot_at        # missed: loose on the goal tile
            messages.append(f"Shot missed ({percent(outcome.shot_chance)} on target)")
        elif outcome.save_chance > 0 and roll() < outcome.save_chance:
            board.ball = outcome.keeper_at      # saved: the keeper catches it
            messages.append(f"Saved by the keeper ({percent(outcome.save_chance)} save chance)")
        else:
            board.ball = outcome.shot_at
            messages.append(f"Goal! ({percent(outcome.score_chance)} chance)")
            return Result(destination, " - ".join(messages), goal_for=destination.player.team,
                          tackle_won=tackle_won)

    return Result(destination, " - ".join(messages), tackle_won=tackle_won,
                  lost_ball=intercepted or lost)


WIN, LOSE = 0.0, 0.999999   # dice rolls that make a chance succeed / fail


def dice_branches(outcome):
    """[(probability, rolls)] for every way an outcome's dice can fall, in the
    order apply_outcome rolls them: a tackle by moving across the carrier
    (won or not, the move goes on), the tackles by opponents the carrier
    goes past (one winning ends the ball's part), interceptions (the first
    success ends them), shot on target, then the keeper's save. Pass the
    rolls (via a scripted roll function) to reproduce that branch exactly."""
    branches = [(1.0, [])]
    failed = []                 # branches where the ball was lost on the way
    if outcome.tackle_from is not None:
        p = outcome.tackle_chance
        branches = ([(q * p, rolls + [WIN]) for q, rolls in branches]
                    + [(q * (1 - p), rolls + [LOSE]) for q, rolls in branches])
    for _, p in outcome.carry_tackles:
        failed += [(q * p, rolls + [WIN]) for q, rolls in branches]
        branches = [(q * (1 - p), rolls + [LOSE]) for q, rolls in branches]
    for _, p in outcome.intercepts:
        failed += [(q * p, rolls + [WIN]) for q, rolls in branches]   # no shot after that
        branches = [(q * (1 - p), rolls + [LOSE]) for q, rolls in branches]
    if outcome.shot_at is not None:
        p, save = outcome.shot_chance, outcome.save_chance
        result = [(q * (1 - p), rolls + [LOSE]) for q, rolls in branches]              # miss
        if save > 0:
            result += [(q * p * save, rolls + [WIN, WIN]) for q, rolls in branches]      # saved
            result += [(q * p * (1 - save), rolls + [WIN, LOSE]) for q, rolls in branches]  # goal
        else:
            result += [(q * p, rolls + [WIN]) for q, rolls in branches]                  # goal
        branches = result
    return [(q, rolls) for q, rolls in branches + failed if q > 0]


# --- Config checking ---

ACTION_KEYS = {"requires", "self", "ball", "lofted", "turn", "face_movement", "shoot",
               "shield"}
EFFECT_KEYS = ("self", "ball", "turn", "shoot", "shield")


def validate_config():
    """Raise ValueError with a clear message if the action config or setup is wrong."""
    def is_offset(value):
        return (isinstance(value, tuple) and len(value) == 2
                and all(isinstance(n, int) for n in value))

    def is_offsets(value, allow_empty=False):
        if isinstance(value, list):
            return (allow_empty or len(value) > 0) and all(is_offset(o) for o in value)
        return is_offset(value)

    def is_chance(value):
        return isinstance(value, (int, float)) and 0 <= value <= 1

    for name, action in config.ACTIONS.items():
        where = f"config.ACTIONS[{name!r}]"
        if "rotate" in action:
            raise ValueError(f"{where} has 'rotate', but players now only face left or right: "
                             f"use \"turn\": True to flip them round.")
        if "cost" in action:
            raise ValueError(f"{where} has a 'cost', but action points are gone: each turn "
                             f"now has free steps plus one action (two with the ball). "
                             f"Remove 'cost'.")
        if "tackle" in action:
            raise ValueError(f"{where} has 'tackle', but there's no tackle action any more: "
                             f"players tackle by moving across the opponent with the ball. "
                             f"Remove the action (or its 'tackle' key), and take it out of "
                             f"any player's action list.")
        unknown = set(action) - ACTION_KEYS
        if unknown:
            raise ValueError(f"{where} has unknown keys {sorted(unknown)}; "
                             f"allowed: {sorted(ACTION_KEYS)}")
        for req in action.get("requires", []):
            if req not in REQUIREMENTS:
                raise ValueError(f"{where} requires unknown {req!r}; "
                                 f"known: {sorted(REQUIREMENTS)}")
        if action.get("ball") == WITH_PLAYER:
            if "self" not in action:
                raise ValueError(f"{where} has \"ball\": \"with_player\" but no 'self' "
                                 f"for the player to move to")
        elif "ball" in action and not is_offsets(action["ball"], allow_empty=True):
            raise ValueError(f"{where}['ball'] must be an offset like (1, 0), a list of "
                             f"them like [(1, 0), (0, 1)], or \"with_player\"")
        if "self" in action and not is_offsets(action["self"]):
            raise ValueError(f"{where}['self'] must be an offset like (1, 0) "
                             f"or a list of them like [(1, 0), (0, 1)]")
        choices = [key for key in ("self", "ball") if isinstance(action.get(key), list)]
        if choices == ["self", "ball"] and action["self"] == action["ball"]:
            raise ValueError(f"{where} gives 'self' and 'ball' the same list, which would be "
                             f"two separate choices. For the ball to go wherever the player "
                             f"goes, use \"ball\": \"with_player\".")
        if len(choices) > 1:
            raise ValueError(f"{where} offers more than one choice ({', '.join(choices)}); "
                             f"only one of a 'self' list or a 'ball' list is allowed")
        if action.get("turn") and action.get("face_movement"):
            raise ValueError(f"{where} has both 'turn' and 'face_movement'; pick one")
        for key in ("shoot", "lofted", "turn", "face_movement"):
            if key in action and not isinstance(action[key], bool):
                raise ValueError(f"{where}[{key!r}] must be True or False")
        if "shield" in action and (isinstance(action["shield"], bool)
                                   or not is_chance(action["shield"])):
            raise ValueError(f"{where}['shield'] must be an amount from 0 to 1, e.g. 0.3 "
                             f"to take 30 percentage points off tackle chances")
        if action.get("ball") == []:
            print(f"Note: config.ACTIONS[{name!r}] has no 'ball' locations yet, so it can't be used.")
        elif not any(action.get(key) for key in EFFECT_KEYS):
            raise ValueError(f"{where} doesn't do anything: give it one of {list(EFFECT_KEYS)}")

    if set(config.TACKLE_CHANCE) != set(TACKLE_ANGLES):
        raise ValueError(f"config.TACKLE_CHANCE needs exactly the keys {TACKLE_ANGLES}")
    if config.AI_PROGRESS_STOP is not None and (
            not isinstance(config.AI_PROGRESS_STOP, int) or config.AI_PROGRESS_STOP < 1):
        raise ValueError("config.AI_PROGRESS_STOP must be None or a whole number, 1 or more")
    if not isinstance(config.FULLSCREEN, bool):
        raise ValueError("config.FULLSCREEN must be True or False")
    if not isinstance(config.DARK_MODE, bool):
        raise ValueError("config.DARK_MODE must be True or False")
    if not isinstance(config.DARK_MODE_COLOURS, dict):
        raise ValueError("config.DARK_MODE_COLOURS must be a dict of colour name -> (r, g, b)")
    for name, colour in config.DARK_MODE_COLOURS.items():
        if not hasattr(config, name):
            raise ValueError(f"config.DARK_MODE_COLOURS: there's no colour called {name} in config")
        if (not isinstance(colour, tuple) or len(colour) not in (3, 4)
                or not all(isinstance(v, int) and 0 <= v <= 255 for v in colour)):
            raise ValueError(f"config.DARK_MODE_COLOURS[{name!r}] must be (r, g, b) "
                             f"or (r, g, b, alpha) with values 0-255")
    if config.TACKLE_MOVE_CHECK not in ("path", "ends"):
        raise ValueError('config.TACKLE_MOVE_CHECK must be "path" or "ends"')
    if hasattr(config, "ZOC_TACKLE_CHANCE"):
        print("Note: config.ZOC_TACKLE_CHANCE isn't used any more (carriers can move straight "
              "away from opponents; going past one risks TACKLE_CHANCE). You can delete it.")
    for table in ("TACKLE_CHANCE", "SHOT_CHANCE"):
        for key, value in getattr(config, table).items():
            if not is_chance(value):
                raise ValueError(f"config.{table}[{key!r}] must be a chance from 0 to 1")
    if not is_chance(config.INTERCEPT_CHANCE):
        raise ValueError("config.INTERCEPT_CHANCE must be a chance from 0 to 1")

    if set(config.ACTIONS_PER_TURN) != set(config.INITIAL_SETUP) or not all(
            isinstance(n, int) and not isinstance(n, bool) and n >= 0
            for n in config.ACTIONS_PER_TURN.values()):
        raise ValueError("config.ACTIONS_PER_TURN needs a whole number (0 or more) for each team, "
                         "e.g. {HOME: 1, AWAY: 1}")
    for name in ("START_POSSESSION_ACTIONS", "TACKLE_POSSESSION_ACTIONS",
                 "RECEIVE_POSSESSION_ACTIONS"):
        value = getattr(config, name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"config.{name} must be a whole number, 0 or more")
    if not isinstance(config.RANKING_TEAMS, int) or not 1 <= config.RANKING_TEAMS <= 100:
        raise ValueError("config.RANKING_TEAMS must be a whole number from 1 to 100")
    for name in ("RANKING_TOP_RATING", "RANKING_POINTS_PER_PLACE", "RANKING_MIN_WIN",
                 "RANKING_WIN_SHARE", "RANKING_DRAW_SHARE", "RANKING_LOSS_MAX", "RANKING_LOSS_HALF_GAP"):
        value = getattr(config, name)
        if not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"config.{name} must be a number, 0 or more")
    if config.RANKING_POINTS_PER_PLACE <= 0:
        raise ValueError("config.RANKING_POINTS_PER_PLACE must be more than 0")
    if not isinstance(config.TURNS_PER_TEAM, int) or config.TURNS_PER_TEAM < 0:
        raise ValueError("config.TURNS_PER_TEAM must be a whole number (0 = no limit)")
    if config.AI_ENGINE not in ("planner", "mcts"):
        raise ValueError("config.AI_ENGINE must be 'planner' or 'mcts'")
    for team, controller in config.CONTROLLERS.items():
        if controller not in ("human", "ai"):
            raise ValueError(f"config.CONTROLLERS[{team!r}] must be 'human' or 'ai'")
    if (not isinstance(config.AI_CHASE_BOOST, dict)
            or set(config.AI_CHASE_BOOST) - {"behind", "level", "ahead"}
            or not all(isinstance(v, (int, float)) and v > -1 for v in config.AI_CHASE_BOOST.values())):
        raise ValueError('config.AI_CHASE_BOOST must look like {"behind": 0.75, "level": 0.25, '
                         '"ahead": 0.0}, each above -1')
    missing = REQUIRED_AI_WEIGHTS - set(config.AI_WEIGHTS)
    unknown = set(config.AI_WEIGHTS) - REQUIRED_AI_WEIGHTS - set(AI_WEIGHT_DEFAULTS)
    if missing or unknown:
        raise ValueError(f"config.AI_WEIGHTS needs the keys {sorted(REQUIRED_AI_WEIGHTS)}, "
                         f"and can also have {sorted(AI_WEIGHT_DEFAULTS)}"
                         + (f" (missing {sorted(missing)})" if missing else "")
                         + (f" (unknown {sorted(unknown)})" if unknown else ""))

    for team, facing in config.TEAM_FACING.items():
        if facing not in (config.RIGHT, config.LEFT):
            raise ValueError(f"config.TEAM_FACING[{team!r}] must be RIGHT or LEFT")
    for entries in config.INITIAL_SETUP.values():
        for entry in entries:
            if "facing" in entry and entry["facing"] not in (config.RIGHT, config.LEFT):
                raise ValueError(f"A player at {entry['position']} has facing {entry['facing']!r}; "
                                 f"it must be RIGHT or LEFT")

    for team, entries in config.INITIAL_SETUP.items():
        if not entries:
            raise ValueError(f"{config.TEAM_NAMES[team]} has no players")
        roles = [entry.get("role", "outfield") for entry in entries]
        for role in roles:
            if role not in ("outfield", "goalkeeper"):
                raise ValueError(f"Unknown role {role!r}: use 'outfield' or 'goalkeeper'")
        if roles.count("goalkeeper") > 1:
            raise ValueError(f"{config.TEAM_NAMES[team]} has more than one goalkeeper")
    for key, value in config.KEEPER_SAVE_CHANCE.items():
        if not isinstance(key, int) or not is_chance(value):
            raise ValueError(f"config.KEEPER_SAVE_CHANCE[{key!r}] must map a distance to a chance from 0 to 1")

    if config.SHIFTED_ROWS not in ("top", "second"):
        raise ValueError('config.SHIFTED_ROWS must be "top" or "second"')
    board_coords = set(rectangle_coords(config.BOARD_COLS, config.BOARD_ROWS,
                                        config.SHIFTED_ROWS == "top"))
    taken = {}
    for team, entries in config.INITIAL_SETUP.items():
        for entry in entries:
            position = entry["position"]
            if position not in board_coords:
                raise ValueError(f"Starting position {position} is off the "
                                 f"{config.BOARD_COLS} x {config.BOARD_ROWS} board")
            if position in taken:
                raise ValueError(f"Two players start on {position} "
                                 f"({config.TEAM_NAMES[taken[position]]} and "
                                 f"{config.TEAM_NAMES[team]}): every player needs their own tile")
            taken[position] = team
    if config.INITIAL_BALL not in board_coords:
        raise ValueError(f"INITIAL_BALL {config.INITIAL_BALL} is off the board")

    limit = config.MAX_PLAYER_ACTIONS
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise ValueError("config.MAX_PLAYER_ACTIONS must be a whole number (0 = no limit)")
    used = list(config.DEFAULT_ACTIONS)
    for team, entries in config.INITIAL_SETUP.items():
        for entry in entries:
            used += entry["actions"]
            count = len(player_actions(entry["actions"]))
            if limit and count > limit:
                raise ValueError(f"The {config.TEAM_NAMES[team]} player starting at "
                                 f"{entry['position']} has {count} actions, more than "
                                 f"MAX_PLAYER_ACTIONS ({limit}). Actions in DEFAULT_ACTIONS count too.")
    for name in used:
        if name not in config.ACTIONS:
            raise ValueError(f"Action {name!r} is given to a player but isn't in config.ACTIONS")
