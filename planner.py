"""Computer player that plans whole turns.

Instead of searching one small move at a time, the planner thinks about a
whole turn at once and checks it against the opponent's best reply:

  1. Candidate turns: a beam search builds complete plans for the team to
     move. It keeps the best few partial plans at each step (by the
     evaluation), drops plans that reach the same position by doing the
     same moves in a different order, and finishes each plan with the
     off-ball positioning steps (ai.positioning_step).
  2. Replies: for the most promising plans, the same beam search builds the
     opponent's best replies from the position each plan leaves.
  3. Choice: each plan is scored by the opponent's best reply against it
     (the reply that's worst for us), with tackles and shots averaged over
     their dice. The best-scoring plan is played.

A plan stops at the first move that rolls dice (a tackle or a shot): what to
do next depends on the result, so the rest of the turn is planned again once
it's known. For scoring, the turn after such a move is finished greedily for
each possible result.

TurnPlayer keeps the current plan and hands out its moves one at a time,
planning again whenever the game isn't where the plan expects.
"""
import math
import time

import config
from actions import dice_branches, outcomes
from ai import action_charge, deterministic, evaluate, positioning_step, search_moves
from game import END_TURN, other_team

MAX_TURN_MOVES = 12             # safety limit on searched moves in one turn


# --- Small helpers ---

def signature(game):
    """Everything that decides what can happen next, so two plans reaching
    the same position (e.g. the same moves in another order) count once."""
    players = frozenset((tile.coord, tile.player.team, tile.player.facing)
                        for tile in game.board.tiles.values() if tile.player is not None)
    return (game.turn_number, game.over, game.active_team, game.board.ball, game.actions_remaining,
            players, frozenset(game.stepped), frozenset(game.had_ball), tuple(sorted(game.score.items())))


def after(game, move, rolls=None):
    """A copy of game with move played, using the given dice rolls if any."""
    copy = game.clone()
    if rolls is not None:
        script = iter(rolls)
        copy.roll = lambda: next(script, 0.0)
    copy.perform(move)
    return copy


def dice_results(game, move):
    """[(probability, rolls)] for each way a tackle/shot move can turn out
    (including the keeper's save roll)."""
    outcome = outcomes(game.board, game.board.get(move.coord), move.action)[move.target]
    return dice_branches(outcome)


def finish_with_positioning(game):
    """Play the off-ball positioning steps on game (in place); returns them."""
    steps = []
    while True:
        step = positioning_step(game)
        if step is None:
            return steps
        game.perform(step)
        steps.append(step)


def ended(game):
    """game with the positioning steps and End turn played: (copy, moves)."""
    copy = game.clone()
    moves = finish_with_positioning(copy)
    copy.perform(END_TURN)
    return copy, moves + [END_TURN]


def quick_value(game, move, team, start_score):
    """Rough value of a move for team: the position straight after it (dice
    averaged), less the small action charge. End turn includes the
    positioning steps."""
    if move == END_TURN:
        return evaluate(ended(game)[0], team, start_score)
    charge = config.AI_ACTION_COST * action_charge(move)
    if deterministic(move):
        return evaluate(after(game, move), team, start_score) - charge
    return sum(p * evaluate(after(game, move, rolls), team, start_score)
               for p, rolls in dice_results(game, move)) - charge


def finish_turn_greedily(game, team, start_score, depth=0):
    """[(probability, end position)] for the rest of the current team's turn,
    picking the best quick_value move each time and following every dice
    result. Ends with positioning and End turn."""
    if game.over:
        return [(1.0, game)]
    if depth >= MAX_TURN_MOVES:
        return [(1.0, ended(game)[0])]
    moves = search_moves(game)
    best = max(moves, key=lambda m: quick_value(game, m, team, start_score))
    if best == END_TURN:
        return [(1.0, ended(game)[0])]
    if deterministic(best):
        return finish_turn_greedily(after(game, best), team, start_score, depth + 1)
    results = []
    for p, rolls in dice_results(game, best):
        state = after(game, best, rolls)
        if state.over or state.active_team != game.active_team:   # a goal: the turn is over
            results.append((p, state))
        else:
            results.extend((p * q, s) for q, s in
                           finish_turn_greedily(state, team, start_score, depth + 1))
    return results


# --- Candidate turns ---

class TurnPlan:
    """A planned turn: the moves to play, and how to work out where it ends."""

    def __init__(self, start, moves, final=None, chance_state=None, score=0.0):
        self.start = start                # position the plan starts from
        self.moves = moves                # moves to play (ends with End turn or a dice move)
        self.final = final                # end position, for a plan ending with End turn
        self.chance_state = chance_state  # position just before the dice move, otherwise
        self.score = score                # quick estimate, for ordering

    def end_positions(self, team, start_score):
        """[(probability, position after this team's turn)]."""
        if self.final is not None:
            return [(1.0, self.final)]
        dice_move = self.moves[-1]
        results = []
        for p, rolls in dice_results(self.chance_state, dice_move):
            state = after(self.chance_state, dice_move, rolls)
            if state.over or state.active_team != self.chance_state.active_team:   # scored: turn over
                results.append((p, state))
            else:
                results.extend((p * q, s) for q, s in finish_turn_greedily(state, team, start_score))
        return results


def candidate_turns(game, width, count, deadline):
    """Up to `count` good plans for the team to move in game, best first by a
    quick estimate, found by a beam search `width` plans wide."""
    team = game.active_team
    start_score = dict(game.score)
    beam = [(game, [])]
    finished = {}
    seen = {signature(game)}
    for _ in range(MAX_TURN_MOVES):
        if not beam or time.perf_counter() > deadline:
            break
        partial = []
        for state, moves in beam:
            for move in search_moves(state):
                if move == END_TURN:
                    final, closing = ended(state)
                    key = signature(final)
                    if key not in finished:
                        finished[key] = TurnPlan(game, moves + closing, final=final,
                                                 score=evaluate(final, team, start_score))
                elif not deterministic(move):
                    value = quick_value(state, move, team, start_score)
                    key = (signature(state), move)
                    if key not in finished:
                        finished[key] = TurnPlan(game, moves + [move], chance_state=state, score=value)
                else:
                    nxt = after(state, move)
                    key = signature(nxt)
                    if key in seen:
                        continue                 # same position reached another way
                    seen.add(key)
                    value = (evaluate(nxt, team, start_score)
                             - config.AI_ACTION_COST * action_charge(move))
                    partial.append((value, nxt, moves + [move]))
        partial.sort(key=lambda item: item[0], reverse=True)
        beam = [(state, moves) for _, state, moves in partial[:width]]
    plans = sorted(finished.values(), key=lambda plan: plan.score, reverse=True)
    return plans[:count]


# --- Choosing a turn ---

def reply_value(position, team, start_score, deadline):
    """Value for team of `position` (the other team to move), assuming the
    other team plays its best reply turn. Dice are averaged."""
    if position.over or position.active_team == team:   # full time, or the game moved on
        return evaluate(position, team, start_score)
    replies = candidate_turns(position, config.AI_REPLY_WIDTH, config.AI_REPLIES_CHECKED, deadline)
    if not replies:
        return evaluate(position, team, start_score)
    opponent = position.active_team
    worst = math.inf
    for reply in replies:
        value = sum(p * evaluate(end, team, start_score)
                    for p, end in reply.end_positions(opponent, start_score))
        worst = min(worst, value)
    return worst


def best_turn(game, seconds=None):
    """(moves, info) for the team to move in game: the plan whose opponent's
    best reply leaves them best off. game isn't changed."""
    seconds = config.AI_PLAN_SECONDS if seconds is None else seconds
    started = time.perf_counter()
    deadline = started + seconds
    team = game.active_team
    start_score = dict(game.score)
    plans = candidate_turns(game, config.AI_PLAN_WIDTH, config.AI_PLANS_CHECKED,
                            started + seconds * 0.3)
    if not plans:
        return [END_TURN], {"plans": 0, "checked": 0}
    best, best_value, checked = plans[0], -math.inf, 0
    for plan in plans:
        if checked and time.perf_counter() > deadline:
            break
        value = 0.0
        for p, position in plan.end_positions(team, start_score):
            value += p * reply_value(position, team, start_score, deadline)
        checked += 1
        if value > best_value:
            best, best_value = plan, value
    return best.moves, {"plans": len(plans), "checked": checked, "value": best_value,
                        "seconds": time.perf_counter() - started}


class TurnPlayer:
    """Plays turns from best_turn, one move at a time. Keeps the plan between
    calls and plans again whenever the game isn't where the plan expects
    (a new turn, or the result of a tackle or shot)."""

    def __init__(self):
        self.queue = []         # [(signature of the position before the move, move)]
        self.last_info = {}

    def next_move(self, game, seconds=None):
        if game.over:
            return END_TURN
        if self.queue and self.queue[0][0] == signature(game):
            return self.queue.pop(0)[1]
        moves, self.last_info = best_turn(game, seconds)
        self.queue = []
        state = game.clone()
        for move in moves:
            self.queue.append((signature(state), move))
            if move == END_TURN or not deterministic(move):
                break           # after dice, plan again from the real result
            state.perform(move)
        return self.queue.pop(0)[1]
