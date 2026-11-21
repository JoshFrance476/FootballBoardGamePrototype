"""Computer player that plans whole turns.

Instead of searching one small move at a time, the planner thinks about a
whole turn at once and checks it against the opponent's best reply:

  1. Candidate turns: a beam search builds complete plans for the team to
     move. It keeps the best few partial plans at each step (by the
     evaluation), drops plans that reach the same position by doing the
     same moves in a different order, and finishes each plan with the
     off-ball positioning steps (ai.positioning_step). The shortlist then
     takes turns between the kinds of plan (keep the ball where it is, move
     it, tackle, shoot) and keeps only a couple of near-identical plans, so
     a pass or a tackle isn't crowded out by twenty ways of doing nothing.
  2. Replies: for the most promising plans, the same beam search builds the
     opponent's best replies from the position each plan leaves.
  3. Choice: each plan is scored by the opponent's best reply against it
     (the reply that's worst for us), with tackles and shots averaged over
     their dice. The best-scoring plan is played.

A plan stops at the first move that rolls dice (a tackle, a shot, or a move
that risks being tackled going past someone, or an interception): what to
do next depends on the result, so the rest of the turn is planned again once
it's known. For scoring, the turn after such a move is finished greedily for
each possible result.

TurnPlayer keeps the current plan and hands out its moves one at a time,
planning again whenever the game isn't where the plan expects.
"""
import math
import time
from collections import Counter

import config
from ai import (action_charge, deterministic, evaluate, move_branches, positioning_step,
                search_moves)
from game import END_TURN, STEP, other_team
from hex_math import neighbours

MAX_TURN_MOVES = 12             # safety limit on searched moves in one turn
PLANS_PER_GROUP = 2             # near-identical plans kept on a shortlist (see plan_group)
SHORTLIST_MARGIN = 0.6          # a kind of plan only gets a place while its plans are within
                                # this much of the best plan's quick estimate


# --- Small helpers ---

def signature(game):
    """Everything that decides what can happen next, so two plans reaching
    the same position (e.g. the same moves in another order) count once."""
    players = frozenset((tile.coord, tile.player.team, tile.player.facing, tile.player.shield)
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
    """[(probability, rolls)] for each way a move with dice can turn out:
    tackles, interceptions, shots and saves."""
    return move_branches(game, move)


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
    if deterministic(move, game):
        return evaluate(after(game, move), team, start_score) - charge
    return sum(p * evaluate(after(game, move, rolls), team, start_score)
               for p, rolls in dice_results(game, move)) - charge


def end_now_value(game, move, team, start_score):
    """Value for team of playing move (dice averaged) and then ending the turn
    straight away. Used to rank plans that stop at a dice move against plans
    that finish the turn: both are then judged at the end of a turn, when
    the opponents are about to move (so the risk of being tackled counts)."""
    charge = config.AI_ACTION_COST * action_charge(move)
    total = 0.0
    for p, rolls in dice_results(game, move):
        state = after(game, move, rolls)
        if not state.over and state.active_team == game.active_team:
            state.perform(END_TURN)
        total += p * evaluate(state, team, start_score)
    return total - charge


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
    if deterministic(best, game):
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
                elif not deterministic(move, state):
                    value = end_now_value(state, move, team, start_score)
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
    return shortlist(finished.values(), count)


def plan_kind(plan):
    """What a plan does with the ball: "shot", "tackle", "ball" (it moves the
    ball, or risks it to a tackle or an interception) or
    "hold" (the ball stays where it is)."""
    last = plan.moves[-1]
    if plan.final is None:                      # it ends with a move that rolls dice
        action = {} if last.action == STEP else config.ACTIONS[last.action]
        if action.get("shoot"):
            return "shot"
        board = plan.chance_state.board
        holder, mover = board.get(board.ball).player, board.get(last.coord).player
        if holder is not None and holder.team != mover.team:
            return "tackle"                     # moving across the carrier
        return "ball"
    def holder(board):
        tile = board.get(board.ball)
        return None if tile.player is None else tile.player.team

    moved = (plan.final.board.ball != plan.start.board.ball
             or holder(plan.final.board) != holder(plan.start.board)
             or plan.final.score != plan.start.score)
    return "ball" if moved else "hold"


def plan_group(plan):
    """Plans with the same group are near-identical: the same dice move from
    the same spot, or the same end position around the ball (off-ball
    players stood a bit differently)."""
    kind = plan_kind(plan)
    if plan.final is None:
        last = plan.moves[-1]
        return kind, last.coord, last.action, last.target, plan.chance_state.board.ball
    board = plan.final.board
    around = frozenset((c, board.get(c).player.team) for c in neighbours(board.ball)
                       if board.get(c) is not None and board.get(c).player is not None)
    return kind, board.ball, around


def shortlist(plans, count):
    """The best `count` plans to check against replies, in the order to check
    them. Kinds of plan (see plan_kind) take turns, best kind first, so each
    kind's best plans are checked early even when the quick estimate rates
    them lower (it can't see what the extra actions from winning the ball
    or receiving a pass lead to). Only PLANS_PER_GROUP plans per plan_group,
    and a kind drops out once its plans are SHORTLIST_MARGIN below the best."""
    ranked = sorted(plans, key=lambda plan: plan.score, reverse=True)
    if not ranked:
        return []
    floor = ranked[0].score - SHORTLIST_MARGIN
    per_group = Counter()
    by_kind = {}
    for plan in ranked:
        key = plan_group(plan)
        if per_group[key] >= PLANS_PER_GROUP:
            continue
        per_group[key] += 1
        by_kind.setdefault(key[0], []).append(plan)
    picked = []
    while len(picked) < count:
        added = False
        for kind in list(by_kind):              # in order of each kind's best plan
            queue = by_kind[kind]
            if queue and len(picked) < count and (not picked or queue[0].score >= floor):
                picked.append(queue.pop(0))
                added = True
        if not added:
            break
    return picked


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
                        "kind": plan_kind(best), "seconds": time.perf_counter() - started}


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
            if move == END_TURN or not deterministic(move, state):
                break           # after dice, plan again from the real result
            state.perform(move)
        return self.queue.pop(0)[1]
