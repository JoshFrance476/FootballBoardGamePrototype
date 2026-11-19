"""Computer player: Monte Carlo Tree Search over single actions.

Each step in the tree is one Move (an action with its target, or End turn),
so the AI picks one action, plays it, then searches again for the next.

One search iteration:
  1. Copy the real game, with its own dice.
  2. Selection: walk down the tree from the root, at each step choosing the
     child with the best UCT score among the moves that are legal *in this
     copy*. Dice are rolled for real as moves are played, so the same path
     can lead to different positions; the tree's statistics average over
     that luck ("open-loop" MCTS).
  3. Expansion: at the first step with an untried legal move, add it.
  4. Playout: play mostly-greedy moves (see playout_move) until the
     lookahead ends (by default the end of the opponent's next turn).
  5. Evaluation: score the final position for the AI's team.
  6. Backpropagation: add that score to every node on the path, from the
     point of view of whichever team made each node's move.
After the time runs out, the most-visited move at the root is played.
"""
import math
import random
import threading
import time

import config
from actions import (AI_WEIGHT_DEFAULTS, LOSE, WIN, WITH_PLAYER, dice_branches,
                     interceptions, keeper_save_chance, meets_requirements, offset_list,
                     outcomes, path_blocked, scoring_chance, shot_chance, shielded,
                     tackle_chance, zoc_tackles)
from game import END_TURN, STEP, Move, other_team
from hex_math import hex_distance, is_in_front, neighbours, offset_for_facing


# --- Evaluation ---

def evaluate(game, team, start_score):
    """How good the position is for team: higher is better, opponents' good is negative.

    Every term is symmetric, so evaluate(game, A) == -evaluate(game, B),
    except for config.AI_CHASE_BOOST (see chase_boost), which raises the
    attacking terms for team alone when it's behind or level.
    Weights come from config.AI_WEIGHTS:
      goal          per goal of difference scored since the search started
      possession    + if team has the ball, - if the opponent does
      loose_ball    when nobody has the ball: up to +/- this, by how much
                    nearer team's closest player is than the opponent's
      progress      for whichever team has the ball: from 0 with the ball on
                    their own goal up to this with it on the goal they attack
                    (+ for team, - for the opponent). A loose ball counts for
                    nobody, so kicking it away upfield gains nothing. Curved
                    by config.AI_PROGRESS_CURVE: above 1, holding the ball in
                    midfield is worth little and the final third a lot.
      shot_chance   the chance a shot from where the ball is goes in (after the
                    keeper's save chance), for whichever
                    team has it (+ for team, - for the opponent)
      ball_safety   minus the best tackle chance the opponents can set up on
                    their next turn: an opponent already next to the carrier,
                    or one who can take their free step next to them, using
                    TACKLE_CHANCE for the side they'd tackle from. Only
                    counted when the opponents move next.
      shot_threat   the best shot chance the carrier's team can set up on its
                    next turn (free step plus carrying the ball with an
                    action, then shooting), times the chance of getting away
                    from the zone of control tackles if the carrier has to
                    move. Only counted when that team moves next.
      support       for the team with the ball: how well the carrier can pass
                    to each teammate (see passing_options): 1 for a safe ball
                    to an unmarked teammate, less for interception risk or a
                    marked receiver, averaged over the teammates. So
                    defenders also gain by standing next to passing lanes.
      trapped       for the team with the ball: minus the chance of losing it
                    to zone of control tackles if the carrier has to move
                    away from the opponents next to them, reduced by how good
                    their best pass out is. So defenders gain by boxing the
                    carrier in, and carriers avoid ending up next to opponents.
      cover         for a team defending against a ball carrier: the fraction
                    of its players between the ball and their own goal
      pressing      for a team defending against a ball carrier: 1 if one of
                    its players is next to the carrier, 0.5 if two tiles away
                    (close enough to step in and tackle), else 0
      runs          for the team with the ball: the fraction of the carrier's
                    teammates who are ahead of the ball and unmarked
    """
    if game.over:
        return final_value(game, team, start_score)
    value = weighted_terms(game, team, start_score)

    # If the opponents move next and can tackle the carrier, the position is
    # worth a mix of keeping the ball and losing it, by the tackle chance.
    # (A fixed penalty undervalued this badly with certain front tackles.)
    carrier_tile = game.board.get(game.board.ball)
    carrier = carrier_tile.player
    if (carrier is not None and game.active_team != carrier.team
            and config.ACTIONS_PER_TURN.get(game.active_team, 1) > 0):
        chance, tackle_at, tackler_at = best_tackle(game.board, carrier_tile)
        if chance > 0:
            lost = value_if_tackled(game, team, start_score, tackle_at, tackler_at)
            value = (1 - chance) * value + chance * lost
    return value


def weight(name):
    """config.AI_WEIGHTS[name], or its default for weights older configs don't have."""
    value = config.AI_WEIGHTS.get(name)
    return AI_WEIGHT_DEFAULTS.get(name, 0.0) if value is None else value


# Terms that reward team's own attack; chase_boost scales these while team has the ball
ATTACK_TERMS = ("progress", "shot_chance", "shot_threat", "runs")


def weighted_terms(game, team, start_score):
    terms = evaluation_terms(game, team, start_score)
    boost = chase_boost(game, team) if terms.get("possession", 0) > 0 else 0.0
    total = 0.0
    for name, amount in terms.items():
        if amount:
            scale = 1 + boost if boost and name in ATTACK_TERMS else 1
            total += weight(name) * scale * amount
    return total


def chase_boost(game, team):
    """How much extra weight team's attacking terms get while it has the ball:
    config.AI_CHASE_BOOST for whether team is "behind", "level" or "ahead"
    on the current score. With a turn limit, half of it at kick-off rising to
    all of it at full time, so a losing team gets more desperate as time runs
    out. 0.5 means the attacking terms count 50% more."""
    mine, theirs = game.score[team], game.score[other_team(team)]
    state = "behind" if mine < theirs else "ahead" if mine > theirs else "level"
    boost = config.AI_CHASE_BOOST.get(state, 0.0)
    if boost and game.turn_limit:
        boost *= 0.5 + 0.5 * min(1.0, game.turn_number / game.turn_limit)
    return boost


def value_if_tackled(game, team, start_score, tackle_at, tackler_at):
    """weighted_terms with the tackler on tackle_at holding the ball (the board
    is put back afterwards)."""
    board = game.board
    ball = board.ball
    moved = tackle_at != tackler_at
    if moved:
        board.move_player(board.get(tackler_at), board.get(tackle_at))
    board.ball = tackle_at
    try:
        return weighted_terms(game, team, start_score)
    finally:
        board.ball = ball
        if moved:
            board.move_player(board.get(tackle_at), board.get(tackler_at))


def final_value(game, team, start_score):
    """Value of a finished match for team: only the score matters. Each goal
    of difference since the search started counts AI_WEIGHTS["goal"], plus
    AI_RESULT_WEIGHT for each step from loss to draw to win (so an equaliser
    or a winner is worth more than a fourth goal)."""
    opponent = other_team(team)

    def result(score):
        return (score[team] > score[opponent]) - (score[team] < score[opponent])

    goals = (game.score[team] - start_score[team]) - (game.score[opponent] - start_score[opponent])
    return (weight("goal") * goals
            + config.AI_RESULT_WEIGHT * (result(game.score) - result(start_score)))


def evaluation_terms(game, team, start_score):
    """The unweighted amount of each evaluation term, for team. evaluate()
    multiplies these by config.AI_WEIGHTS and adds them up. Handy for seeing
    why the AI likes or dislikes a position."""
    board = game.board
    opponent = other_team(team)
    terms = dict.fromkeys(set(config.AI_WEIGHTS) | set(AI_WEIGHT_DEFAULTS), 0.0)
    occupied = [tile for tile in board.tiles.values() if tile.player is not None]

    goals_for = game.score[team] - start_score[team]
    goals_against = game.score[opponent] - start_score[opponent]
    terms["goal"] = goals_for - goals_against

    # +1 if team has the ball, -1 if the opponent does, 0 if it's loose
    carrier_tile = board.get(board.ball)
    carrier = carrier_tile.player
    side = 0 if carrier is None else (1 if carrier.team == team else -1)
    terms["possession"] = side
    if carrier is None:
        terms["loose_ball"] = loose_ball_race(board, team, occupied)

    if carrier is not None:
        terms["progress"] = progress(board, carrier.team) ** config.AI_PROGRESS_CURVE * side
        terms["shot_chance"] = scoring_chance(board, carrier.team, board.ball) * side
        if game.active_team != carrier.team:   # the defenders move next: can they tackle?
            terms["ball_safety"] = -tackle_threat(board, carrier_tile) * side
        else:                                   # the carrier's team moves next: can it shoot?
            terms["shot_threat"] = shot_threat(board, carrier_tile) * side
        options = passing_options(board, carrier_tile, occupied)
        mates = sum(1 for tile in occupied if tile.player.team == carrier.team) - 1
        terms["support"] = (sum(options.values()) / mates if mates else 0.0) * side
        best_pass = max(options.values(), default=0.0)
        terms["trapped"] = -escape_loss(board, carrier_tile) * (1 - best_pass) * side
        terms["runs"] = runs(board, carrier_tile, occupied) * side

    terms["cover"] = cover(board, team, occupied) - cover(board, opponent, occupied)
    terms["pressing"] = pressing(board, team, occupied) - pressing(board, opponent, occupied)
    return terms


def occupied_tiles(board):
    return [tile for tile in board.tiles.values() if tile.player is not None]


def loose_ball_race(board, team, occupied=None):
    """For a loose ball: from +1 (team is right on it, the opponent far away)
    to -1, by comparing each team's closest player to the ball."""
    nearest = {}
    for tile in occupied if occupied is not None else occupied_tiles(board):
        if tile.player is not None:
            d = hex_distance(tile.coord, board.ball)
            nearest[tile.player.team] = min(d, nearest.get(tile.player.team, d))
    mine = nearest.get(team)
    theirs = nearest.get(other_team(team))
    if mine is None or theirs is None:
        return 0.0
    return (theirs - mine) / max(theirs + mine, 1)


def carrier_exposure(board, carrier_tile):
    """Best tackle chance any opponent next to the ball carrier has (0 if none)."""
    team = carrier_tile.player.team
    best = 0.0
    for coord in neighbours(carrier_tile.coord):
        tile = board.get(coord)
        if tile is not None and tile.player is not None and tile.player.team != team:
            best = max(best, tackle_chance(carrier_tile, tile))
    return best


def tackle_threat(board, carrier_tile):
    """Best tackle chance the carrier's opponents can get on their next turn:
    from a tile next to the carrier that an opponent is on already or can
    step to (it must be empty), scored by which side of the carrier it is."""
    return best_tackle(board, carrier_tile)[0]


def best_tackle(board, carrier_tile):
    """(chance, tile the tackle is made from, where that opponent is now) for
    the opponents' best tackle on the carrier next turn, as in tackle_threat.
    (0.0, None, None) if there's none."""
    team = carrier_tile.player.team
    facing = carrier_tile.player.facing
    best = (0.0, None, None)
    for direction, coord in enumerate(neighbours(carrier_tile.coord)):
        tile = board.get(coord)
        if tile is None:
            continue
        side = "front" if is_in_front(facing, direction) else "behind"
        chance = shielded(config.TACKLE_CHANCE[side], carrier_tile.player)
        if chance <= best[0]:
            continue
        if tile.player is not None:
            if tile.player.team != team:                  # an opponent already there
                best = (chance, coord, coord)
            continue
        for c in neighbours(coord):                       # an opponent who can step there
            other = board.get(c)
            if other is not None and other.player is not None and other.player.team != team:
                best = (chance, coord, c)
                break
    return best


def shot_threat(board, carrier_tile):
    """Best chance of scoring the carrier's team can set up on its next turn:
    a shot from where the carrier is, or from up to 1 + carry_range() tiles
    nearer the goal (free step, then an action that moves them with the
    ball), which needs them to get away from any zone of control tackles
    first. Ignores blockers."""
    player = carrier_tile.player
    team = player.team
    distance = hex_distance(board.ball, board.goal_attacked_by(team))
    on_target = 1 - keeper_save_chance(board, other_team(team))
    here = config.SHOT_CHANCE.get(distance, 0.0) * on_target
    closest = max(1, distance - 1 - carry_range())
    moved = config.SHOT_CHANCE.get(closest, 0.0) * on_target
    if moved > here:
        moved *= escape_keep(board, carrier_tile.coord, team, player.facing, player.shield)
    return max(here, moved)


def escape_keep(board, coord, team, facing, shield=0.0):
    """Chance that a carrier of team on coord, facing `facing`, keeps the ball
    when moving away: every opponent next to them gets a zone of control
    tackle (ZOC_TACKLE_CHANCE by side, less the shield)."""
    keep = 1.0
    for direction, near in enumerate(neighbours(coord)):
        tile = board.get(near)
        if tile is not None and tile.player is not None and tile.player.team != team:
            side = "front" if is_in_front(facing, direction) else "behind"
            keep *= 1 - max(0.0, config.ZOC_TACKLE_CHANCE[side] - shield)
    return keep


def escape_loss(board, carrier_tile):
    """Chance the carrier loses the ball to zone of control tackles if they move away."""
    player = carrier_tile.player
    return 1 - escape_keep(board, carrier_tile.coord, player.team, player.facing, player.shield)


_kick_cache = {}


def kicks(name, facing):
    """(lofted, [(dq, dr), ...]) for an action that kicks the ball to a chosen
    tile (a pass, lob, cross...), its offsets turned for facing. None for an
    action that doesn't (carrying the ball, tackling, shooting...)."""
    action = config.ACTIONS.get(name)
    key = (name, facing, id(action))
    if key not in _kick_cache:
        ball = None if action is None else action.get("ball")
        if ball is None or ball == WITH_PLAYER:
            _kick_cache[key] = None
        else:
            _kick_cache[key] = (bool(action.get("lofted")),
                                [offset_for_facing(o, facing) for o in offset_list(ball)])
    return _kick_cache[key]


def passing_options(board, carrier_tile, occupied=None):
    """{teammate's coord: how good a pass to them is, 0 to 1} for each teammate
    the carrier could kick the ball to next with one of their own actions,
    either where the teammate stands or after the teammate's free step to an
    empty tile next to them. The value is the chance the ball gets through
    (1 for a lofted ball, otherwise the chance no opponent intercepts it),
    halved if an opponent stands next to the receiver (they'd be trapped).
    Paths blocked by an opponent don't count."""
    player = carrier_tile.player
    team = player.team
    occupied = occupied if occupied is not None else occupied_tiles(board)
    receivers = {}              # tile a pass could go to -> (teammate's coord, needs a step)
    for tile in occupied:
        if tile.player.team != team or tile is carrier_tile:
            continue
        receivers[tile.coord] = (tile.coord, False)
        for coord in neighbours(tile.coord):
            spot = board.get(coord)
            if spot is not None and spot.player is None and coord not in receivers:
                receivers[coord] = (tile.coord, True)
    if not receivers:
        return {}

    def marked(coord, mate):
        return any(n != mate and board.get(n) is not None and board.get(n).player is not None
                   and board.get(n).player.team != team for n in neighbours(coord))

    q, r = carrier_tile.coord
    best = {}
    for name in player.actions:
        kick = kicks(name, player.facing)
        if kick is None or not meets_requirements(board, carrier_tile, config.ACTIONS[name]):
            continue
        lofted, offsets = kick
        for dq, dr in offsets:
            coord = (q + dq, r + dr)
            if coord not in receivers:
                continue
            mate, stepped = receivers[coord]
            if lofted:
                value = 1.0
            elif path_blocked(board, player, carrier_tile.coord, coord):
                continue
            else:
                value = 1.0
                for _, chance in interceptions(board, team, carrier_tile.coord, coord):
                    value *= 1 - chance
            if marked(coord, mate):
                value *= 0.5
            if stepped:
                value *= 0.9                    # needs the receiver's step first
            if value > best.get(mate, 0.0):
                best[mate] = value
    return best


def clear_caches():
    """Forget everything worked out from config.ACTIONS. Call after editing
    actions while the program is running (the menu's editors do)."""
    _carry_range_cache.clear()
    _pass_range_cache.clear()
    _pure_turn_cache.clear()
    _kick_cache.clear()


_carry_range_cache = {}


def carry_range():
    """Furthest any action moves a player together with the ball, in tiles."""
    key = (id(config.ACTIONS), len(config.ACTIONS))
    if key not in _carry_range_cache:
        _carry_range_cache.clear()
        best = 0
        for action in config.ACTIONS.values():
            if "self" not in action or "ball" not in action:
                continue
            selves = offset_list(action["self"])
            if action["ball"] == WITH_PLAYER or offset_list(action["ball"]) == selves:
                best = max([best] + [hex_distance((0, 0), o) for o in selves])
        _carry_range_cache[key] = best
    return _carry_range_cache[key]


_pass_range_cache = {}


def pass_range():
    """Furthest any non-lofted pass in config.ACTIONS reaches, in tiles."""
    key = (id(config.ACTIONS), len(config.ACTIONS))
    if key not in _pass_range_cache:
        _pass_range_cache.clear()
        _pass_range_cache[key] = max(
            (hex_distance((0, 0), offset)
             for action in config.ACTIONS.values() if "ball" in action and not action.get("lofted")
             for offset in offset_list(action["ball"])), default=0)
    return _pass_range_cache[key]


def open_teammates(board, carrier_tile, occupied=None):
    """Fraction of the carrier's teammates who are open for a pass: within
    pass_range() of the carrier, with no opponent on the line between them,
    and with no opponent standing next to them."""
    team = carrier_tile.player.team
    reach = pass_range()
    occupied = occupied if occupied is not None else occupied_tiles(board)
    mates = [tile for tile in occupied if tile.player.team == team and tile is not carrier_tile]
    if not mates:
        return 0.0

    def marked(tile):
        return any(board.get(c) is not None and board.get(c).player is not None
                   and board.get(c).player.team != team for c in neighbours(tile.coord))

    open_count = sum(1 for tile in mates
                     if hex_distance(tile.coord, carrier_tile.coord) <= reach
                     and not path_blocked(board, carrier_tile.player, carrier_tile.coord, tile.coord)
                     and not marked(tile))
    return open_count / len(mates)


def progress(board, team):
    """How far up the pitch the ball is for team: 0 on the goal team defends,
    up to 1 next to the goal it attacks. The goal tile itself counts the same
    as the tiles next to it: you can't shoot from it, so it's no better."""
    to_attack = max(1, hex_distance(board.ball, board.goal_attacked_by(team)))
    to_defend = hex_distance(board.ball, board.goal_attacked_by(other_team(team)))
    return min(1.0, (to_defend + 1) / max(to_defend + to_attack, 1))


def pressing(board, team, occupied=None):
    """When the opponent has the ball: 1 if one of team's players is next to
    the carrier, 0.5 if the nearest is two tiles away, 0 otherwise."""
    carrier = board.get(board.ball).player
    if carrier is None or carrier.team == team:
        return 0.0
    occupied = occupied if occupied is not None else occupied_tiles(board)
    nearest = min((hex_distance(tile.coord, board.ball) for tile in occupied
                   if tile.player.team == team), default=99)
    return 1.0 if nearest == 1 else 0.5 if nearest == 2 else 0.0


def runs(board, carrier_tile, occupied=None):
    """Fraction of the carrier's teammates who are nearer the goal they attack
    than the ball is, with no opponent standing next to them."""
    team = carrier_tile.player.team
    occupied = occupied if occupied is not None else occupied_tiles(board)
    goal = board.goal_attacked_by(team)
    ball_gap = hex_distance(board.ball, goal)
    mates = [tile for tile in occupied if tile.player.team == team and tile is not carrier_tile]
    if not mates:
        return 0.0

    def unmarked(tile):
        return not any(board.get(c) is not None and board.get(c).player is not None
                       and board.get(c).player.team != team for c in neighbours(tile.coord))

    ahead = sum(1 for tile in mates if hex_distance(tile.coord, goal) < ball_gap and unmarked(tile))
    return ahead / len(mates)


def cover(board, team, occupied=None):
    """When the opponent has the ball: fraction of team's players nearer their
    own goal than the ball is, i.e. in position to defend. 0 otherwise."""
    carrier = board.get(board.ball).player
    if carrier is None or carrier.team == team:
        return 0.0
    own_goal = board.goal_attacked_by(other_team(team))
    ball_gap = hex_distance(board.ball, own_goal)
    occupied = occupied if occupied is not None else occupied_tiles(board)
    players = [tile for tile in occupied if tile.player.team == team]
    if not players:
        return 0.0
    return sum(1 for tile in players if hex_distance(tile.coord, own_goal) < ball_gap) / len(players)


# --- Moves worth considering ---

_pure_turn_cache = {}


def is_pure_turn(action_name):
    """True for an action that only flips the player round (no movement, ball,
    tackle or shot)."""
    action = config.ACTIONS[action_name]
    key = (action_name, id(action))
    if key not in _pure_turn_cache:
        _pure_turn_cache[key] = bool(action.get("turn")) and not any(
            action.get(k) for k in ("self", "ball", "tackle", "shoot"))
    return _pure_turn_cache[key]


def wasted_turn(game, tile, action_name):
    """True if this would flip a player straight back round: a pure turn
    right after their last action was also a pure turn. Two flips undo each
    other, so the second can only waste the action."""
    if action_name == STEP or not is_pure_turn(action_name):
        return False
    last = game.last_actions.get(tile.coord)
    return last is not None and is_pure_turn(last)


def near_ball(game, tile):
    """True if this player is within config.AI_FOCUS_RADIUS tiles of the ball."""
    return hex_distance(tile.coord, game.board.ball) <= config.AI_FOCUS_RADIUS


def search_moves(game):
    """The moves the tree search considers: legal moves, minus turns that
    would only flip a player straight back (see wasted_turn), and minus free
    steps for players away from the ball, which positioning_step handles."""
    moves = []
    for move in game.legal_moves():
        if move.coord is not None:
            tile = game.board.get(move.coord)
            if move.action == STEP and not near_ball(game, tile):
                continue
            if wasted_turn(game, tile, move.action):
                continue
        moves.append(move)
    return moves


def positioning_step(game):
    """The best free step for a player away from the ball: whichever step
    raises the evaluation most, if by at least config.AI_POSITION_MIN_GAIN.
    None if no step for such a player helps (they stay where they are).

    This replaces searching those steps: far from the ball they mostly
    affect support, runs, cover and pressing, which the evaluation measures
    directly, so a quick one-step check chooses them well and leaves the
    search's time for the ball."""
    team = game.active_team
    before = dict(game.score)
    base = evaluate(game, team, before)
    best, best_gain = None, config.AI_POSITION_MIN_GAIN
    saved = game.snapshot()
    for tile in game.own_tiles():
        if not game.can_step(tile) or near_ball(game, tile):
            continue
        for target in game.step_targets(tile):
            move = Move(tile.coord, STEP, target)
            game.perform(move)
            gain = evaluate(game, team, before) - base
            game.restore(saved)
            if gain > best_gain:
                best, best_gain = move, gain
    return best


def move_branches(game, move):
    """[(probability, rolls)] for every way the move can turn out in game:
    a tackle, zone of control tackles, interceptions, a shot and the keeper's
    save. [(1.0, [])] if it rolls no dice. Pass the rolls (via a scripted roll
    function) to reproduce a branch exactly."""
    if move == END_TURN:
        return [(1.0, [])]
    tile = game.board.get(move.coord)
    if move.action == STEP:
        if game.board.ball != move.coord:
            return [(1.0, [])]
        branches, keep = [], 1.0
        rolls = []
        for _, chance in zoc_tackles(game.board, tile):    # the carrier steps away
            branches.append((keep * chance, rolls + [WIN]))
            keep *= 1 - chance
            rolls = rolls + [LOSE]
        branches.append((keep, rolls))
        return [(p, r) for p, r in branches if p > 0]
    if not may_roll(move.action):
        return [(1.0, [])]
    return dice_branches(outcomes(game.board, tile, move.action)[move.target])


def may_roll(action_name):
    """Quick check: could this action ever roll dice with the current config?"""
    action = config.ACTIONS[action_name]
    if action.get("tackle") or action.get("shoot"):
        return True
    if action.get("lofted"):
        return False            # lofted balls and carries escape both
    if action.get("ball") == WITH_PLAYER:
        return "self" in action and any(config.ZOC_TACKLE_CHANCE.values())
    return "ball" in action and config.INTERCEPT_CHANCE > 0


def deterministic(move, game):
    """True if the move rolls no dice in game (no tackle, shot, zone of
    control tackle or interception)."""
    return move_branches(game, move) == [(1.0, [])]


def action_charge(move):
    """1 for a move that uses one of the team's actions, 0 for a step or End turn."""
    return 0 if move == END_TURN or move.action == STEP else 1


# --- Playouts ---

def move_kinds(game):
    """(tile, "step" or action name) pairs the team could use right now,
    skipping turns that would only flip a player straight back."""
    kinds = []
    for tile in game.own_tiles():
        if game.can_step(tile):
            kinds.append((tile, STEP))
        if game.can_act(tile):
            kinds.extend((tile, name) for name in tile.player.actions
                         if not wasted_turn(game, tile, name))
    return kinds


def random_of_kind(game, tile, kind, rng):
    """A random move of this kind for this player, or None if there isn't one."""
    if kind == STEP:
        targets = game.step_targets(tile)
        return Move(tile.coord, STEP, rng.choice(targets)) if targets else None
    options = outcomes(game.board, tile, kind)
    return Move(tile.coord, kind, rng.choice(list(options))) if options else None


def random_move(game, rng):
    """A random legal move, cheaply: try random (player, step-or-action)
    pairs until one is possible, then pick a random target. End turn when
    nothing is."""
    kinds = move_kinds(game)
    rng.shuffle(kinds)
    for tile, kind in kinds:
        move = random_of_kind(game, tile, kind, rng)
        if move is not None:
            return move
    return END_TURN


def playout_move(game, rng):
    """The move a simulated player makes during a playout.

    Usually greedy: gather End turn plus config.AI_PLAYOUT_CANDIDATES random
    legal moves, try each on a copy, and keep whichever the evaluation likes
    best for the team moving. With chance config.AI_PLAYOUT_RANDOM, just a
    random move instead, so playouts still explore.
    """
    if rng.random() < config.AI_PLAYOUT_RANDOM:
        return random_move(game, rng)

    team = game.active_team
    kinds = move_kinds(game)
    rng.shuffle(kinds)
    candidates = [END_TURN]
    for tile, kind in kinds:
        move = random_of_kind(game, tile, kind, rng)
        if move is not None:
            candidates.append(move)
            if len(candidates) > config.AI_PLAYOUT_CANDIDATES:
                break

    best, best_value = END_TURN, None
    before = dict(game.score)
    saved = game.snapshot()
    for move in candidates:
        game.perform(move)                      # try it...
        value = evaluate(game, team, before)
        game.restore(saved)                     # ...and take it back
        if best_value is None or value > best_value:
            best, best_value = move, value
    return best


# --- Move ordering ---

def rank_moves(game, moves, rng):
    """(moves sorted best first, {move: value}) for the team to move, by
    trying each and evaluating the result (minus the action charge).
    Used so the search looks at plausible moves first and adds the rest
    gradually, and to give new moves a starting value."""
    team = game.active_team
    scored = []
    for move in moves:
        if move.action == STEP and move.coord == game.board.ball:
            value = step_then_act_value(game, move, team)
        else:
            value = expected_value(game, move, team) - config.AI_ACTION_COST * action_charge(move)
        scored.append((value, move))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [move for _, move in scored], {move: value for value, move in scored}


def step_then_act_value(game, move, team):
    """For the ball carrier's free step: the value after the step, or after
    the step plus the best single action that follows it if that's higher.
    So a step that sets up a shot (or a pass, or a dribble) is valued for
    what it makes possible, and the search tries "step, then act"."""
    before = dict(game.score)
    saved = game.snapshot()
    game.perform(move)
    best = evaluate(game, team, before)
    tile = game.board.get(move.target)
    if game.actions_left() > 0 and tile.player is not None:
        for name in tile.player.actions:
            for target in outcomes(game.board, tile, name):
                follow = Move(tile.coord, name, target)
                best = max(best, expected_value(game, follow, team) - config.AI_ACTION_COST)
    game.restore(saved)
    return best


def expected_value(game, move, team):
    """Evaluation for team straight after move. For tackles and shots, the
    average over success and failure weighted by their chances, so a shot
    isn't judged on one lucky or unlucky roll."""
    def value_with(rolls):
        script = iter(rolls)
        before = dict(game.score)
        saved, roll = game.snapshot(), game.roll
        game.roll = lambda: next(script, 0.0)
        game.perform(move)                      # try it...
        value = evaluate(game, team, before)
        game.restore(saved)                     # ...and take it back
        game.roll = roll
        return value

    return sum(p * value_with(rolls) for p, rolls in move_branches(game, move))


def widened(node):
    """How many of a node's best-ranked moves the search may consider so far.
    Grows with visits (progressive widening), so weak moves are only tried
    once the strong ones have been looked at properly."""
    return max(1, math.ceil(config.AI_WIDENING * (node.visits + 1) ** config.AI_WIDENING_POWER))


# --- Search tree ---

class Node:
    __slots__ = ("move", "parent", "mover", "children", "visits", "total", "ranked",
                 "priors", "fixed", "legal")

    def __init__(self, move=None, parent=None, mover=None, fixed=True):
        self.move = move          # the move that led here (None at the root)
        self.parent = parent
        self.mover = mover        # the team that made that move
        self.children = {}        # Move -> Node
        self.visits = 0
        self.total = 0.0          # sum of evaluations, from mover's point of view
        self.ranked = None        # moves from here, best first by a quick one-step check
        self.priors = None        # move -> that quick check's value, for starting new children
        self.fixed = fixed        # True if no dice were rolled on the way here from the root,
                                  # so the position here is always the same
        self.legal = None         # legal moves here, remembered when fixed is True

    def uct(self, exploration):
        mean = self.total / self.visits
        return mean + exploration * math.sqrt(math.log(self.parent.visits) / self.visits)


class SearchResult:
    def __init__(self, move, iterations, root):
        self.move = move
        self.iterations = iterations
        self.root = root

    def subtree_after(self, move, game):
        """The part of this search tree that follows `move`, to start the next
        search from, so the plan carries over. None if the move involved dice
        (the statistics below it mix outcomes that didn't all happen) or ended
        the turn."""
        if move == END_TURN or not deterministic(move, game):
            return None
        child = self.root.children.get(move)
        if child is not None:
            child.parent = None
        return child

    def summary(self, top=5):
        """Human-readable list of the most-visited root moves, for debugging."""
        children = sorted(self.root.children.values(), key=lambda n: n.visits, reverse=True)
        lines = [f"{self.iterations} iterations"]
        for node in children[:top]:
            mean = node.total / node.visits
            lines.append(f"  {node.move.action:>14} {node.move.coord}->{node.move.target}"
                         f"  visits {node.visits:5}  value {mean:+.3f}")
        return "\n".join(lines)


def search(game, seconds=None, max_iterations=None, rng=None, root=None):
    """Choose a move for the team whose turn it is in game. game isn't changed.

    Stops after `seconds` (default config.AI_THINK_SECONDS) or
    `max_iterations`, whichever comes first. Pass root (from a previous
    result's subtree_after) to carry on from an earlier search's plan.

    Each simulation's score also includes a small charge of
    config.AI_ACTION_COST per action each team uses on moves chosen in the
    tree (against that team), so an action has to actually help to be worth
    using. Steps and moves in playouts aren't charged.

    Progressive widening: at each node the moves are ranked once by a quick
    one-step evaluation (rank_moves), and only the best widened(node) of
    them are considered, more as the node gets visited. Without this, every
    node's average was dragged down by trying each of ~25 moves, mostly
    poor ones, which made the AI's own actions look worse than ending its
    turn (where the poor moves explored are the opponent's).
    """
    rng = rng or random.Random()
    seconds = config.AI_THINK_SECONDS if seconds is None else seconds
    team = game.active_team
    start_score = dict(game.score)
    horizon = game.turn_number + config.AI_LOOKAHEAD_TURNS
    exploration = config.AI_EXPLORATION
    action_cost = config.AI_ACTION_COST
    opponent = other_team(team)

    root = root if root is not None else Node()
    legal = search_moves(game)
    if len(legal) == 1:                 # nothing to think about
        return SearchResult(legal[0], 0, root)

    deadline = time.perf_counter() + seconds
    iterations = 0
    while time.perf_counter() < deadline and (max_iterations is None or iterations < max_iterations):
        iterations += 1
        sim = game.clone(roll=rng.random)
        node = root
        spent = {team: 0, opponent: 0}      # actions used on tree moves in this simulation

        def play(move, charge=True):
            if charge:
                spent[sim.active_team] += action_charge(move)
            sim.perform(move)

        # Selection and expansion
        while not sim.over and sim.turn_number < horizon:
            if node.legal is not None:
                legal = node.legal
            else:
                legal = set(search_moves(sim))
                if node.fixed:
                    node.legal = legal
            if node.ranked is None:
                node.ranked, node.priors = rank_moves(sim, list(legal), rng)
            # The best few moves still legal here (dice can change what's legal)
            allowed = [m for m in node.ranked if m in legal][:widened(node)]
            if not allowed:                     # nothing ranked is legal in this copy
                allowed = list(legal)
            untried = [m for m in allowed if m not in node.children]
            if untried:
                move = untried[0]               # best-ranked move not yet tried
                child = Node(move, node, sim.active_team, node.fixed and deterministic(move, sim))
                # Start from the quick check's value, as if already visited a couple
                # of times, so one unlucky early roll can't bury a good move
                child.visits = config.AI_PRIOR_VISITS
                child.total = node.priors.get(move, 0.0) * config.AI_PRIOR_VISITS
                node.children[move] = child
                play(move)
                node = child
                break
            node = max((node.children[m] for m in allowed), key=lambda n: n.uct(exploration))
            play(node.move)

        # Playout
        while not sim.over and sim.turn_number < horizon:
            play(playout_move(sim, rng), charge=False)

        # Backpropagation
        value = evaluate(sim, team, start_score) - action_cost * (spent[team] - spent[opponent])
        root.visits += 1
        while node is not root:
            node.visits += 1
            node.total += value if node.mover == team else -value
            node = node.parent

    best = max(root.children.values(), key=lambda n: n.visits)
    return SearchResult(best.move, iterations, root)


def choose(game, seconds=None, max_iterations=None, rng=None, root=None):
    """The AI's next move: a positioning step for a player away from the ball
    if one helps (instant), otherwise the tree search's choice."""
    step = positioning_step(game)
    if step is not None:
        return SearchResult(step, 0, Node())
    return search(game, seconds, max_iterations, rng, root)


# --- Playing in the live game ---

class AIController:
    """Plays the turns of computer-controlled teams in the live game.

    Searching happens on a background thread (on a copy of the game) so the
    window keeps redrawing. Once a move is chosen it's shown for
    config.AI_MOVE_DELAY seconds - player selected, action highlighted,
    chosen tile marked - before being played.
    """

    def __init__(self, seed=config.AI_SEED, controllers=None):
        self.controllers = controllers  # {team: "human" or "ai"}; None = config.CONTROLLERS
        self.rng = random.Random(seed)
        self.thread = None
        self.result = None
        self.pending = None       # chosen Move waiting to be shown and played
        self.play_at = 0.0
        self.plan = None          # search tree to continue from after the pending move
        self.plan_turn = None     # the turn that plan belongs to
        self.turn_player = None   # planner.TurnPlayer, when config.AI_ENGINE is "planner"
        self.verbose = config.AI_DEBUG

    def controls(self, team):
        return (self.controllers or config.CONTROLLERS)[team] == "ai"

    @property
    def highlight(self):
        """The tile the AI is about to click, for the renderer to mark."""
        return self.pending.target if self.pending is not None else None

    def update(self, game):
        """Call once per frame. Advances the AI if it's a computer team's turn."""
        if game.over or not self.controls(game.active_team):
            return

        # A chosen move has been on show long enough: play it
        if self.pending is not None:
            if time.perf_counter() >= self.play_at:
                self.plan_turn = game.turn_number
                game.perform(self.pending)
                game.selected = None
                game.action = None
                self.pending = None
            return

        # No search running yet: start one on a copy of the game
        if self.thread is None:
            snapshot = game.clone()
            seed = self.rng.random()
            plan = self.plan if self.plan_turn == game.turn_number else None
            self.plan = None
            self.thread = threading.Thread(target=self._search, args=(snapshot, seed, plan), daemon=True)
            self.thread.start()
            return

        # Search finished: show the chosen move before playing it
        if not self.thread.is_alive():
            self.thread = None
            result, self.result = self.result, None
            if self.verbose:
                print(f"{config.TEAM_NAMES[game.active_team]}: {result.summary()}")
            self.pending = result.move
            self.plan = result.subtree_after(result.move, game)
            self.play_at = time.perf_counter() + config.AI_MOVE_DELAY
            if result.move != END_TURN:
                game.selected = game.board.get(result.move.coord)
                # For a step, selecting the player shows their step tiles; the
                # chosen one is marked by highlight
                game.action = None if result.move.action == STEP else result.move.action

    def _search(self, snapshot, seed, plan):
        if config.AI_ENGINE == "planner":
            if self.turn_player is None:
                from planner import TurnPlayer      # here, not at the top: planner imports ai
                self.turn_player = TurnPlayer()
            move = self.turn_player.next_move(snapshot)
            self.result = SearchResult(move, 0, Node())
            if self.verbose and self.turn_player.last_info:
                print(f"planner: {self.turn_player.last_info}")
                self.turn_player.last_info = {}
        else:
            self.result = choose(snapshot, rng=random.Random(seed), root=plan)
