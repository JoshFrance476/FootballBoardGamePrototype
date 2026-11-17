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
from actions import offset_list, outcomes, path_blocked, shot_chance, tackle_chance
from game import END_TURN, Move, other_team
from hex_math import hex_distance, neighbours


# --- Evaluation ---

def evaluate(game, team, start_score):
    """How good the position is for team: higher is better, opponents' good is negative.

    Every term is symmetric, so evaluate(game, A) == -evaluate(game, B).
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
      shot_chance   the shot chance from where the ball is, for whichever
                    team has it (+ for team, - for the opponent)
      ball_safety   minus the best tackle chance any adjacent opponent has
                    against the ball carrier (uses TACKLE_CHANCE, so facing
                    away from opponents is safer). Only counted when the
                    carrier's opponents move next: a carrier whose own team
                    is about to move can still get away.
      support       for the team with the ball: the fraction of the carrier's
                    teammates who are open for a pass (see open_teammates)
      cover         for a team defending against a ball carrier: the fraction
                    of its players between the ball and their own goal
    """
    weights = config.AI_WEIGHTS
    return sum(weights[name] * amount for name, amount in evaluation_terms(game, team, start_score).items())


def evaluation_terms(game, team, start_score):
    """The unweighted amount of each evaluation term, for team. evaluate()
    multiplies these by config.AI_WEIGHTS and adds them up. Handy for seeing
    why the AI likes or dislikes a position."""
    board = game.board
    opponent = other_team(team)
    terms = dict.fromkeys(config.AI_WEIGHTS, 0.0)
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
        terms["shot_chance"] = (shot_chance(board, carrier.team, board.ball) or 0) * side
        if game.active_team != carrier.team:   # only the team about to move can tackle
            terms["ball_safety"] = -carrier_exposure(board, carrier_tile) * side
        terms["support"] = open_teammates(board, carrier_tile, occupied) * side

    terms["cover"] = cover(board, team, occupied) - cover(board, opponent, occupied)
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
    1 on the goal it attacks."""
    to_attack = hex_distance(board.ball, board.goal_attacked_by(team))
    to_defend = hex_distance(board.ball, board.goal_attacked_by(other_team(team)))
    return to_defend / max(to_defend + to_attack, 1)


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

_pure_rotation_cache = {}


def is_pure_rotation(action_name):
    """True for an action that only turns the player (no movement, ball, tackle or shot)."""
    action = config.ACTIONS[action_name]
    key = (action_name, id(action))
    if key not in _pure_rotation_cache:
        _pure_rotation_cache[key] = bool(action.get("rotate")) and not any(
            action.get(k) for k in ("self", "ball", "tackle", "shoot"))
    return _pure_rotation_cache[key]


def wasted_rotation(game, tile, action_name):
    """True if this would be a second turn in a row for the same player, when
    one free rotation ("rotate": "any") could have done both."""
    if not is_pure_rotation(action_name):
        return False
    last = game.last_actions.get(tile.coord)
    if last is None or not is_pure_rotation(last):
        return False
    return any(config.ACTIONS[name].get("rotate") == "any" and is_pure_rotation(name)
               for name in tile.player.actions)


def search_moves(game):
    """Legal moves, minus ones that can only waste action points (see wasted_rotation)."""
    return [move for move in game.legal_moves()
            if move.coord is None
            or not wasted_rotation(game, game.board.get(move.coord), move.action)]


def deterministic(move):
    """True if the move involves no dice (no tackle, no shot)."""
    if move == END_TURN:
        return True
    action = config.ACTIONS[move.action]
    return not action.get("tackle") and not action.get("shoot")


# --- Playouts ---

def random_move(game, rng):
    """A random legal move, cheaply: try random (player, action) pairs until
    one can be used, then pick one of its targets. End turn when nothing can."""
    pairs = [(tile, name) for tile in game.own_tiles()
             for name in tile.player.actions
             if game.affordable(name) and not wasted_rotation(game, tile, name)]
    rng.shuffle(pairs)
    for tile, name in pairs:
        options = outcomes(game.board, tile, name)
        if options:
            return Move(tile.coord, name, rng.choice(list(options)))
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
    pairs = [(tile, name) for tile in game.own_tiles()
             for name in tile.player.actions
             if game.affordable(name) and not wasted_rotation(game, tile, name)]
    rng.shuffle(pairs)
    candidates = [END_TURN]
    for tile, name in pairs:
        options = outcomes(game.board, tile, name)
        if options:
            candidates.append(Move(tile.coord, name, rng.choice(list(options))))
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
    trying each and evaluating the result (minus the action point charge).
    Used so the search looks at plausible moves first and adds the rest
    gradually, and to give new moves a starting value."""
    team = game.active_team
    scored = []
    for move in moves:
        cost = 0 if move == END_TURN else config.ACTIONS[move.action]["cost"]
        scored.append((expected_value(game, move, team) - config.AI_AP_COST * cost, move))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [move for _, move in scored], {move: value for value, move in scored}


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

    if deterministic(move):
        return value_with([])
    outcome = outcomes(game.board, game.board.get(move.coord), move.action)[move.target]
    WIN, LOSE = 0.0, 0.999999           # rolls that make a chance succeed / fail
    tackle = outcome.tackle_chance if outcome.tackle_from is not None else None
    shot = outcome.shot_chance if outcome.shot_at is not None else None
    if tackle is not None and shot is not None:
        return (tackle * shot * value_with([WIN, WIN]) + tackle * (1 - shot) * value_with([WIN, LOSE])
                + (1 - tackle) * value_with([LOSE]))
    chance = tackle if tackle is not None else shot
    return chance * value_with([WIN]) + (1 - chance) * value_with([LOSE])


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

    def subtree_after(self, move):
        """The part of this search tree that follows `move`, to start the next
        search from, so the plan carries over. None if the move involved dice
        (the statistics below it mix outcomes that didn't all happen) or ended
        the turn."""
        if move == END_TURN or not deterministic(move):
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
    config.AI_AP_COST per action point each team spends on moves chosen in
    the tree (against that team), so an action has to actually help to be
    worth its cost. Moves in playouts aren't charged.

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
    ap_cost = config.AI_AP_COST
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
        spent = {team: 0, opponent: 0}      # action points used in this simulation

        def play(move, charge=True):
            if charge and move != END_TURN:
                spent[sim.active_team] += config.ACTIONS[move.action]["cost"]
            sim.perform(move)

        # Selection and expansion
        while sim.turn_number < horizon:
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
                child = Node(move, node, sim.active_team, node.fixed and deterministic(move))
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
        while sim.turn_number < horizon:
            play(playout_move(sim, rng), charge=False)

        # Backpropagation
        value = evaluate(sim, team, start_score) - ap_cost * (spent[team] - spent[opponent])
        root.visits += 1
        while node is not root:
            node.visits += 1
            node.total += value if node.mover == team else -value
            node = node.parent

    best = max(root.children.values(), key=lambda n: n.visits)
    return SearchResult(best.move, iterations, root)


# --- Playing in the live game ---

class AIController:
    """Plays the turns of computer-controlled teams in the live game.

    Searching happens on a background thread (on a copy of the game) so the
    window keeps redrawing. Once a move is chosen it's shown for
    config.AI_MOVE_DELAY seconds - player selected, action highlighted,
    chosen tile marked - before being played.
    """

    def __init__(self, seed=config.AI_SEED):
        self.rng = random.Random(seed)
        self.thread = None
        self.result = None
        self.pending = None       # chosen Move waiting to be shown and played
        self.play_at = 0.0
        self.plan = None          # search tree to continue from after the pending move
        self.plan_turn = None     # the turn that plan belongs to
        self.verbose = config.AI_DEBUG

    @staticmethod
    def controls(team):
        return config.CONTROLLERS[team] == "ai"

    @property
    def highlight(self):
        """The tile the AI is about to click, for the renderer to mark."""
        return self.pending.target if self.pending is not None else None

    def update(self, game):
        """Call once per frame. Advances the AI if it's a computer team's turn."""
        if not self.controls(game.active_team):
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
            self.plan = result.subtree_after(result.move)
            self.play_at = time.perf_counter() + config.AI_MOVE_DELAY
            if result.move != END_TURN:
                game.selected = game.board.get(result.move.coord)
                game.action = result.move.action

    def _search(self, snapshot, seed, plan):
        self.result = search(snapshot, rng=random.Random(seed), root=plan)
