# --- Settings ---
SCREEN_SIZE = (800, 600)
HEX_SIZE = 30         # centre-to-corner radius of each drawn hex
HEX_GAP = 4           # gap between neighbouring hex edges in pixels (0 = touching)
BOARD_COLS = 13
BOARD_ROWS = 9
ORIGIN = (60, 60)     # pixel position of the centre of hex (0, 0)
BALL_RADIUS = 8
BORDER_WIDTH = 4      # default tile border thickness in pixels
SELECTED_BORDER_WIDTH = 5
FONT_NAME = "bahnschrift"
FONT_SIZE = 32
UI_POSITION = (10, 480)     # team and action points; status message follows it
SCORE_Y = 6                 # score, centred across the top of the screen

# Action buttons, laid out left to right along the bottom of the screen,
# with the End turn button at the right-hand end of the status line above.
# Buttons shrink below BUTTON_SIZE's width if a player has too many to fit.
BUTTON_SIZE = (120, 44)
BUTTON_GAP = 8        # space between buttons
BUTTON_MARGIN = 12    # space between the buttons and the screen's edges
BUTTON_FONT_SIZE = 18 # also used for the score and status message
BUTTON_RADIUS = 6     # corner rounding
BUTTON_PADDING = 6    # minimum space between a button's label and its edge

# Previews shown while choosing where an action goes
PREVIEW_BALL_WIDTH = 2    # outline thickness of the ball's preview position
PREVIEW_PLAYER_INSET = 9  # how far inside the tile edge the player's preview outline sits
PREVIEW_PLAYER_WIDTH = 3  # thickness of the player's preview outline

# --- Colours ---
PITCH_GREEN = (0, 128, 0)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
BLUE = (0, 0, 255)
RED = (255, 0, 0)
YELLOW = (255, 255, 0)
DARK_GREY = (60, 60, 60)
LIGHT_GREY = (200, 200, 200)
MID_GREY = (140, 140, 140)
LIGHT_BLUE = (130, 160, 255)
LIGHT_RED = (255, 140, 140)

BALL_COLOUR = WHITE
BACKGROUND_COLOUR = WHITE
SELECTED_COLOUR = YELLOW
MOVE_TARGET_COLOUR = BLACK

BUTTON_COLOUR = DARK_GREY
BUTTON_TEXT_COLOUR = WHITE
BUTTON_ACTIVE_COLOUR = YELLOW         # the action currently being targeted
BUTTON_ACTIVE_TEXT_COLOUR = BLACK
BUTTON_DISABLED_COLOUR = LIGHT_GREY   # can't be used right now
BUTTON_DISABLED_TEXT_COLOUR = MID_GREY

# --- Teams ---
HOME = 0
AWAY = 1
TEAM_NAMES = {HOME: "Home", AWAY: "Away"}
TEAM_COLOURS = {HOME: BLUE, AWAY: RED}

# --- Goals ---
# Each goal is the tile in the middle of the left or right edge of the pitch.
ATTACKING_GOAL = {HOME: "right", AWAY: "left"}       # the goal each team shoots at
GOAL_COLOURS = {"left": LIGHT_BLUE, "right": LIGHT_RED}  # tinted for the defending team

# --- Rules ---
ACTION_POINTS_PER_TURN = 8   # each team's budget, spent on any of its players

# Moving turns a player to face the direction they moved in. For moves that
# aren't in a straight hex line, that's the nearest of the six directions;
# a move exactly between two keeps the one closer to their old facing.
# An action's own "rotate" setting overrides this.
FACE_MOVEMENT = True

# Chance a tackle wins the ball, by where the tackler stands relative to the
# way the ball carrier is facing.
TACKLE_CHANCE = {
    "head on": 0.8,      # on the tile the carrier is facing
    "front side": 0.6,   # on either tile next to that one
    "back side": 0.4,    # on either tile next to the one behind them
    "behind": 0.2,       # on the tile directly behind them
}

# Chance a shot scores, by distance from the goal in tiles. A player
# further away than any distance listed here can't shoot.
SHOT_CHANCE = {
    1: 0.8,
    2: 0.4,
    3: 0.2,
}

# Actions a player can take. Each one combines any of these effects (it
# needs at least one), applied in this order:
#   "tackle":   True to try to take the ball from an opponent on a
#               neighbouring tile onto the player's tile, with a chance from
#               TACKLE_CHANCE. Only usable when an opponent next to the
#               player has the ball; click that opponent to do it. If it
#               fails, the rest of the action doesn't happen. If it works,
#               the player has the ball for the rest of the action.
#   "self":     offset the player moves by. The tile must be empty.
#   "ball":     offset the ball moves to, from the player's tile. Only
#               applies when the player has the ball, so "self" and "ball"
#               set to the same offset dribbles the ball along.
#               Either can be a list of offsets, e.g. [(2, 0), (1, 1)], to
#               let the player choose: each valid option is highlighted and
#               they click the one they want. Options that are off the board
#               (or, for "self", occupied) are left out.
#               Moves can't pass through an opponent: if the straight line
#               from the player's tile to the destination touches a tile
#               with an opponent on it (even just an edge or corner), that
#               option isn't allowed. Teammates don't block, and a ball can
#               still be passed onto an opponent's tile.
#   "lofted":   True to let this action's ball fly over opponents, so it
#               can't be blocked.
#   "rotate":   turns to make, 60 degrees clockwise each (1 = right,
#               -1 = left, 3 = about-face), or "any" to let the player
#               choose by clicking the tile they want to face. Counted from
#               the way the player faced before the action.
#               Without "rotate", a player who moves ends up facing the
#               way they moved (see FACE_MOVEMENT).
#   An action can offer only one choice: a "self" list, a "ball" list or
#   "rotate": "any", so one click always settles it.
#   "shoot":    True to shoot at the goal, with a chance from SHOT_CHANCE.
#               The player must have the ball at their feet by this point.
#               A goal sends everyone back to kick-off and gives the next
#               turn to the team that conceded. A miss leaves the ball loose
#               on the goal tile. Opponents in the way don't block shots.
# Plus:
#   "cost":     action points it uses. Required.
#   "requires": list of conditions that must hold first. Leave it out (or
#               use []) for actions allowed whether or not you have the ball.
#                 "possession"    - the player has the ball
#                 "no_possession" - the player doesn't have the ball
#               (more can be added in actions.REQUIREMENTS)
#
# Offsets are axial (dq, dr) steps written as if the player faces east,
# turned to match the way they actually face before the action:
#   (1, 0) forward      (-1, 0) back
#   (1, -1) forward-left  (0, 1) forward-right
#   (0, -1) back-left     (-1, 1) back-right
# Bigger numbers go further, e.g. (2, 0) is two steps forward.
ACTIONS = {
    "reposition": {             # step forward, dribbling the ball if you have it
        "cost": 1,
        "self": (1, 0),
        "ball": (1, 0),
    },
    "sprint": {                 # two steps forward, only without the ball
        "cost": 2,
        "requires": ["no_possession"],
        "self": (3, 0),
    },
    "pass": {
        "cost": 1,
        "requires": ["possession"],
        "ball": (3, 0),
    },
    "lob": {                    # long ball over the top: can't be blocked
        "cost": 2,
        "requires": ["possession"],
        "ball": (5, 0),
        "lofted": True,
    },
    "pass and move": {
        "cost": 2,
        "requires": ["possession"],
        "self": (2, 0),
        "ball": [(1, 2), (3, -2)]
    },
    "drag back": {              # step back with the ball and turn to face the other way
        "cost": 1,
        "requires": ["possession"],
        "self": (-1, 0),
        "ball": (-1, 0),
        "rotate": 3,
    },
    "tackle": {                 # try to win the ball from an adjacent opponent
        "cost": 2,
        "tackle": True,
    },
    "shoot": {
        "cost": 2,
        "requires": ["possession"],
        "shoot": True,
    },
    "rotate": {
        "cost": 1,
        "rotate": "any",
    },
}

# Actions every player gets, added after the ones listed in INITIAL_SETUP
DEFAULT_ACTIONS = ["rotate"]

# Direction each team's players face at kick-off (index into hex_math.DIRECTIONS)
EAST, WEST = 0, 3
TEAM_FACING = {HOME: EAST, AWAY: WEST}

# Facing marker: a small triangle inside the tile, pointing the way the
# player faces. Distances are pixels from the tile centre.
FACING_COLOUR = WHITE
FACING_TIP = 20        # how far out the point is
FACING_BASE = 12       # how far out the flat back edge is
FACING_HALF_WIDTH = 6  # half the width of the back edge

# Kick-off positions, by team. Every key here is a team.
# "actions" are names from ACTIONS, shown as buttons in this order.
# Add "facing": <direction index> to an entry to override TEAM_FACING.
OUTFIELD_ACTIONS = ["reposition", "sprint", "pass","lob", "pass and move", "drag back", "tackle", "shoot"]
INITIAL_SETUP = {
    HOME: [
        {"actions": OUTFIELD_ACTIONS, "position": (1, 2)},
        {"actions": OUTFIELD_ACTIONS, "position": (-1, 6)},
        {"actions": OUTFIELD_ACTIONS, "position": (3, 4)},
    ],
    AWAY: [
        {"actions": OUTFIELD_ACTIONS, "position": (9, 2)},
        {"actions": OUTFIELD_ACTIONS, "position": (7, 6)},
        {"actions": OUTFIELD_ACTIONS, "position": (5, 4)},
    ],
}
INITIAL_BALL = (4, 4)

# --- Computer players ---
# Who plays each team: "human" or "ai". Set both to "ai" to watch.
CONTROLLERS = {HOME: "ai", AWAY: "ai"}

AI_THINK_SECONDS = 1.0    # search time before each action
AI_MOVE_DELAY = 0.6       # seconds the chosen action is shown before it's played
AI_LOOKAHEAD_TURNS = 2    # turn changes to simulate before judging: 2 = to the end
                          # of the opponent's next turn
AI_EXPLORATION = 0.25     # UCT exploration constant: higher spreads the search over more
                          # alternatives. Moves usually differ by ~0.05 in value, so 1.0
                          # spread it so evenly the choice was near-random; 0.25 tested best.
AI_AP_COST = 0.01         # small charge per action point spent, so an action has to
                          # help to be chosen; stops near-pointless moves winning on noise
AI_WIDENING = 2.0         # progressive widening: a position's moves are ranked by a
AI_WIDENING_POWER = 0.5   # quick check and the search considers the best
                          # AI_WIDENING * visits ** AI_WIDENING_POWER of them
AI_PRIOR_VISITS = 2       # new moves start with this many pretend visits at their quick-check
                          # value, so an unlucky first roll (e.g. a missed 80% shot) can't bury them
AI_PLAYOUT_CANDIDATES = 4  # random moves each simulated player weighs up per step
AI_PLAYOUT_RANDOM = 0.2   # chance a simulated move is purely random instead
AI_SEED = None            # set to a number to make the AI's choices repeatable
AI_DEBUG = False          # print the AI's top candidate moves to the console

# Shape of the "progress" term: the fraction of the way up the pitch, raised to
# this power. 1 = every tile worth the same; 2 = midfield worth a quarter of the
# weight and each tile in the final third worth more, so the AI risks the ball
# to attack instead of sitting on it.
AI_PROGRESS_CURVE = 2.0

# How the AI judges a position at the end of its lookahead. See ai.evaluate.
# Every term counts for the team it describes and against its opponent.
AI_WEIGHTS = {
    "goal": 2.0,           # per goal scored (or conceded) during the lookahead
    "possession": 0.1,     # having the ball
    "loose_ball": 0.15,    # nobody has it: your nearest player closer to it than theirs
    "progress": 0.8,       # holding the ball: 0 at your own goal up to this at theirs (curved)
    "shot_chance": 0.1,    # times the shot chance from where the ball is, for its holder
    "ball_safety": 0.12,   # minus the best tackle chance against your carrier, if they can be tackled next
    "support": 0.1,        # fraction of the carrier's teammates open for a pass
    "cover": 0.1,          # defending a carrier: fraction of players goal-side of the ball
}
# Keep possession + progress + shot_chance well below "goal": holding the ball
# next to goal has to be worth less than an 80% shot, or the AI stops shooting.
# "goal" is 2.0 because after a goal the other team kicks off and usually has
# the ball by the end of the lookahead; at 1.0 an unopposed 80% shot looked
# worse than just keeping the ball.
# With these values and AI_PROGRESS_CURVE 2: holding the ball in midfield is
# worth ~0.3, a tile gained ~0.07 in midfield and ~0.13 in the final third, and
# the AI shoots from 1-2 tiles. Lower progress or a curve of 1 make it sit on
# the ball: holding safely beats the risk of losing it going forward.
