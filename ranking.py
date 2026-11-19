"""The ranking ladder: config.RANKING_TEAMS computer teams with fixed ratings,
and you, starting one place below the bottom team. You can challenge any
team rated above you; the result moves your rating (and so your place) and
nothing else. Not saved: a new ladder starts each time the game opens.

Ratings: team #1 has config.RANKING_TOP_RATING, and each place below it is
config.RANKING_POINTS_PER_PLACE lower. You're ranked above any team with
the same rating or lower. With gap = their rating minus yours (they're
always above you, so it's positive):

    win   +max(RANKING_MIN_WIN, RANKING_WIN_SHARE * gap)
    draw  +RANKING_DRAW_SHARE * gap
    loss  -max(1, RANKING_LOSS_MAX * RANKING_LOSS_HALF_GAP / (RANKING_LOSS_HALF_GAP + gap))

So beating a team far above jumps you further than beating one just above,
and losing to a team just above costs more than losing to one far above
(at a gap of RANKING_LOSS_HALF_GAP the loss is half of RANKING_LOSS_MAX).
"""
import config

WIN, DRAW, LOSS = "win", "draw", "loss"


class Ranking:
    def __init__(self):
        self.reset()

    def reset(self):
        """Back to the bottom of a ladder built from the current settings."""
        self.size = config.RANKING_TEAMS
        self.top = config.RANKING_TOP_RATING
        self.per_place = config.RANKING_POINTS_PER_PLACE
        self.rating = max(0, self.team_rating(self.size) - self.per_place)
        self.results = []            # [(team number, result, points change)], latest last

    def out_of_date(self):
        """True if the ladder settings changed since it was built (reset it)."""
        return (self.size, self.top, self.per_place) != (
            config.RANKING_TEAMS, config.RANKING_TOP_RATING, config.RANKING_POINTS_PER_PLACE)

    # --- The table ---

    def team_rating(self, number):
        """Rating of computer team `number` (1 is the top team)."""
        return max(0, self.top - (number - 1) * self.per_place)

    @staticmethod
    def team_name(number):
        return f"Team {number}"

    def place(self, rating=None):
        """Your place in the table (1 is top) with your rating, or the given one."""
        rating = self.rating if rating is None else rating
        return 1 + sum(1 for n in range(1, self.size + 1) if self.team_rating(n) > rating)

    def rows(self):
        """The table top to bottom: [(place, team number or None for you, name, rating)]."""
        you = self.place()
        rows = []
        for n in range(1, self.size + 1):
            place = n if n < you else n + 1
            rows.append((place, n, self.team_name(n), self.team_rating(n)))
        rows.insert(you - 1, (you, None, "You", self.rating))
        return rows

    def at_top(self):
        return self.place() == 1

    def can_challenge(self, number):
        """Only teams above you; nobody once you're #1."""
        return self.team_rating(number) > self.rating

    # --- Results ---

    def change(self, number, result):
        """Points you'd gain (+) or lose (-) for this result against team `number`."""
        gap = self.team_rating(number) - self.rating
        if result == WIN:
            return round(max(config.RANKING_MIN_WIN, config.RANKING_WIN_SHARE * gap))
        if result == DRAW:
            return round(config.RANKING_DRAW_SHARE * gap)
        half = config.RANKING_LOSS_HALF_GAP
        loss = config.RANKING_LOSS_MAX * half / (half + gap) if half + gap > 0 else config.RANKING_LOSS_MAX
        return -max(1, round(loss))

    def place_after(self, number, result):
        return self.place(max(0, self.rating + self.change(number, result)))

    def record(self, number, result):
        """Apply a result against team `number`: (points change, old place, new place)."""
        before = self.place()
        points = self.change(number, result)
        self.rating = max(0, self.rating + points)
        self.results.append((number, result, points))
        return points, before, self.place()


def result_for(score, you):
    """WIN, DRAW or LOSS for team `you` from a final score {team: goals}."""
    mine = score[you]
    theirs = sum(goals for team, goals in score.items() if team != you)
    return WIN if mine > theirs else LOSS if mine < theirs else DRAW
