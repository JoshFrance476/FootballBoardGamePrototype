# --- Settings ---
SCREEN_SIZE = (800, 600)
HEX_SIZE = 30         # centre-to-corner radius of each drawn hex (smaller if the
                      # pitch wouldn't fit; it's always centred on the screen)
HEX_GAP = 4           # gap between neighbouring hex edges in pixels (0 = touching)
BOARD_COLS = 13      # keep both odd so there's a middle row for the goals and a centre spot
BOARD_ROWS = 9
BALL_RADIUS = 8
BORDER_WIDTH = 4      # default tile border thickness in pixels
SELECTED_BORDER_WIDTH = 5
FONT_NAME = "bahnschrift"
FONT_SIZE = 32
UI_POSITION = (10, 480)     # whose turn and what's left; status message follows it
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
STEP_TARGET_COLOUR = (120, 230, 255)   # pale cyan: tiles the selected player can step to
FULL_TIME_SHADE = (0, 0, 0, 120)          # dims the board at full time (RGBA)
FULL_TIME_PANEL_COLOUR = (240, 240, 240)
FULL_TIME_PANEL_SIZE = (440, 170)
STEP_TARGET_WIDTH = 3

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
KEEPER_COLOURS = {HOME: (0, 0, 140), AWAY: (150, 0, 0)}   # darker shade for goalkeepers

# --- Goals ---
# Each goal is the tile in the middle of the left or right edge of the pitch.
ATTACKING_GOAL = {HOME: "right", AWAY: "left"}       # the goal each team shoots at
GOAL_COLOURS = {"left": LIGHT_BLUE, "right": LIGHT_RED}  # tinted for the defending team
BOX_COLOUR = (0, 108, 0)   # slightly darker grass for each goalkeeper's box

# --- Rules ---
# Each turn: every player may take one free step to an empty neighbouring
# tile (carrying the ball if they have it), and the team gets one action for
# any of its players, plus one more each time a player gets the ball for the
# first time that turn (holding it at the start of the turn counts; so do
# receiving a pass, a tackle, or picking up a loose ball). See game.py.

# Extra actions the team earns the first time each player has the ball in a
# turn (on top of the one action every turn starts with), by how they got it:
START_POSSESSION_ACTIONS = 1    # a player has the ball when the turn starts
TACKLE_POSSESSION_ACTIONS = 2   # a player wins the ball with a tackle
RECEIVE_POSSESSION_ACTIONS = 1  # any other way: receiving a pass, picking up a loose ball

# Match length: turns for each team. Turns alternate (the team that concedes
# takes the next turn), so the match ends after twice this many turns.
# 0 = no limit.
TURNS_PER_TEAM = 15

# Facing: players only face right or left. The free step turns a player to
# face the side they stepped towards (straight up/down keeps their facing).
# Actions never change facing unless they have "turn": True.
RIGHT, LEFT = 0, 3   # same values as hex_math.RIGHT / hex_math.LEFT

# Chance a tackle wins the ball, by which side of the ball carrier the
# tackler is on. "front" is the three neighbouring tiles on the side the
# carrier is facing, "behind" the three on the other side.
TACKLE_CHANCE = {
    "front": 0.8,
    "behind": 0.4,
}

# Goalkeepers (a player with "role": "goalkeeper" in INITIAL_SETUP, at most
# one per team). The box is every tile within KEEPER_BOX_RADIUS of a team's
# own goal. Goalkeeping only works in the box: when a shot is on target, the
# defending keeper gets a save roll from KEEPER_SAVE_CHANCE by their distance
# from goal (none outside the box), and catches the ball if they save it.
KEEPER_BOX_RADIUS = 2
KEEPER_SAVE_CHANCE = {
    0: 0.2,   # on the goal tile
    1: 0.1,   # next to it
    2: 0.05,  # two tiles out
}

# Chance a shot is on target, by distance from the goal in tiles (the keeper
# may still save it). A player further away than any distance listed here
# can't shoot.
SHOT_CHANCE = {
    1: 1.0,
    2: 0.5,
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
#               "ball": "with_player" sends the ball wherever the player
#               moves, so a "self" list becomes a single choice for both,
#               e.g. a dribble in a chosen direction (see "dribble" below).
#               Moves can't pass through an opponent: if the straight line
#               from the player's tile to the destination touches a tile
#               with an opponent on it (even just an edge or corner), that
#               option isn't allowed. Teammates don't block, and a ball can
#               still be passed onto an opponent's tile.
#   "lofted":   True to let this action's ball fly over opponents, so it
#               can't be blocked. With "ball": "with_player", the player
#               goes over them too while carrying the ball (the destination
#               must still be empty). Without the ball, the move is blocked
#               as normal.
#   "turn":     True to flip the player round to face the other way at the
#               end of the action.
#   "face_movement": True to end facing the side the player moved towards,
#               like the free step (straight up/down keeps their facing).
#               Without "turn" or "face_movement", an action never changes
#               which way the player faces, even if it moves them.
#   An action can offer only one choice: a "self" list or a "ball" list, so
#   one click always settles it.
#   "shoot":    True to shoot at the goal, with a chance from SHOT_CHANCE.
#               The player must have the ball at their feet by this point.
#               A goal sends everyone back to kick-off and gives the next
#               turn to the team that conceded. A miss leaves the ball loose
#               on the goal tile. Opponents in the way don't block shots.
# Plus:
#   "requires": list of conditions that must hold first. Leave it out (or
#               use []) for actions allowed whether or not you have the ball.
#                 "possession"    - the player has the ball
#                 "no_possession" - the player doesn't have the ball
#                 "goalkeeper"    - the player is a goalkeeper
#                 "in_own_box"    - the player is in their own box
#               (more can be added in actions.REQUIREMENTS)
#
# Offsets are axial (dq, dr) steps written for a player facing right. For a
# player facing left they're mirrored left-to-right, so "forward" means
# towards whichever way they face and up/down on screen stay the same:
#   (1, 0) forward         (-1, 0) back
#   (1, -1) forward-up     (0, 1) forward-down
#   (0, -1) back-up        (-1, 1) back-down
# Bigger numbers go further, e.g. (2, 0) is two steps forward. To let a
# player go diagonally as well as straight, give a list, e.g.
# "self": [(1, -1), (1, 0), (0, 1)] for forward-up, forward or forward-down.
ACTIONS = {
    "reposition": {             # move one tile any way, with the ball if you have it, facing that way
        "self": [(1, 0), (-1, 0), (1, -1), (0, 1), (0, -1), (-1, 1)],
        "ball": "with_player",
        "face_movement": True,
    },
    "sprint": {                 # two steps forward, only without the ball
        "requires": ["no_possession"],
        "self": [(3, -2), (1, 2), (3, -1), (2, 1), (2, -3), (-1, 3)],
    },
    "pass": {
        "requires": ["possession"],
        "ball": [(2, 0), (0, 2), (2, -2), (3, -3), (3, 0), (0, 3)],
    },
    "lob": {                    # long ball over the top: can't be blocked
        "requires": ["possession"],
        "ball": [(5, 0), (0, 5), (5, -5), (4, -4), (4, 0), (0, 4)],
        "lofted": True,
    },
    "pass and move": {
        "requires": ["possession"],
        "self": (2, 0),
        "ball": [(1, 2), (3, -2), (3, -3), (0, 3)],
    },
    "dribble": {                # run with the ball, forward-up or forward-down
        "requires": ["possession"],
        "self": [(2, -1), (1, 1), (2, -2), (0, 2)],
        "ball": "with_player",
        "lofted": True,
    },
    "backpass": {
        "requires": ["possession"],
        "ball": [(-1, -1), (-2, 1), (0, -2), (-2, 2)],
        "lofted": True,
        "turn": True,
    },
    "tackle": {                 # try to win the ball from an adjacent opponent
        "tackle": True,
    },
    "shoot": {
        "requires": ["possession"],
        "shoot": True,
    },
    "turn": {                   # flip round to face the other way
        "turn": True,
    },
    # Goalkeeping actions: keeper only, in their own box. Fill in the "ball"
    # locations; with an empty list the action can't be used yet.
    "drop-kick": {
        "requires": ["possession", "goalkeeper", "in_own_box"],
        "ball": [(5, 0), (2, 3), (5, -3), (6, -2), (4, 2)],
        "lofted": True,
    },
    "throw": {
        "requires": ["possession", "goalkeeper", "in_own_box"],
        "ball": [(1, 1), (2, -1), (3, -1), (2, 1), (2, -3), (-1, 3), (3, -2), (1, 2)],
    },
    "turn and run": {
        "requires": ["possession"],
        "self": (-2, 0),
        "ball": "with_player",
        "turn": True,
    },
}

# Actions every player gets, added after the ones listed in INITIAL_SETUP.
# (Turning uses the turn's action; a free step to the other side also turns them.)
DEFAULT_ACTIONS = ["turn"]

# Which way each team's players face at kick-off
TEAM_FACING = {HOME: RIGHT, AWAY: LEFT}

# Facing marker: a small triangle inside the tile, pointing the way the
# player faces. Distances are pixels from the tile centre.
FACING_COLOUR = WHITE
FACING_TIP = 20        # how far out the point is
FACING_BASE = 12       # how far out the flat back edge is
FACING_HALF_WIDTH = 6  # half the width of the back edge

# Kick-off positions, by team. Every key here is a team.
# "actions" are names from ACTIONS, shown as buttons in this order.
# Add "facing": RIGHT or LEFT to an entry to override TEAM_FACING, and
# "role": "goalkeeper" for a keeper.
OUTFIELD_ACTIONS = ["reposition", "dribble", "sprint", "pass", "lob", "pass and move", "backpass",
                    "tackle", "shoot"]
KEEPER_ACTIONS = ["reposition", "pass", "drop-kick", "throw", "tackle"]

# 2-2-1 formation plus a goalkeeper on each goal tile, Away a mirror image of
# Home. On this 13 x 9 pitch the goals are (-2, 4) and (10, 4) and the centre
# spot is (4, 4).
INITIAL_SETUP = {
    HOME: [                                                   # attacking right
        {"actions": KEEPER_ACTIONS, "position": (-2, 4), "role": "goalkeeper"},
        {"actions": OUTFIELD_ACTIONS, "position": (0, 3)},    # defender, top
        {"actions": OUTFIELD_ACTIONS, "position": (-1, 5)},   # defender, bottom
        {"actions": OUTFIELD_ACTIONS, "position": (3, 1)},    # midfielder, top wing
        {"actions": OUTFIELD_ACTIONS, "position": (0, 7)},    # midfielder, bottom wing
        {"actions": OUTFIELD_ACTIONS, "position": (3, 4)},    # striker, next to the ball
    ],
    AWAY: [                                                   # attacking left
        {"actions": KEEPER_ACTIONS, "position": (10, 4), "role": "goalkeeper"},
        {"actions": OUTFIELD_ACTIONS, "position": (9, 3)},    # defender, top
        {"actions": OUTFIELD_ACTIONS, "position": (8, 5)},    # defender, bottom
        {"actions": OUTFIELD_ACTIONS, "position": (8, 1)},    # midfielder, top wing
        {"actions": OUTFIELD_ACTIONS, "position": (5, 7)},    # midfielder, bottom wing
        {"actions": OUTFIELD_ACTIONS, "position": (5, 4)},    # striker, next to the ball
    ],
}
INITIAL_BALL = (4, 4)   # centre spot

# --- Computer players ---
# Who plays each team: "human" or "ai". Set both to "ai" to watch.
CONTROLLERS = {HOME: "human", AWAY: "ai"}

# Which computer player to use:
#   "planner" - plans whole turns and checks them against the opponent's best
#               reply (planner.py). Thinks once per turn, then plays the plan.
#   "mcts"    - Monte Carlo Tree Search, one move at a time (ai.py).
AI_ENGINE = "planner"
AI_PLAN_SECONDS = 5.0     # planner: thinking time at the start of a turn (and after dice)
AI_PLAN_WIDTH = 16        # planner: partial plans kept at each step of the turn search
AI_PLANS_CHECKED = 20     # planner: best plans checked against the opponent's replies
AI_REPLY_WIDTH = 8        # planner: partial plans kept when searching the opponent's replies
AI_REPLIES_CHECKED = 4    # planner: best replies tried against each plan (the worst one counts)

AI_THINK_SECONDS = 1.0    # mcts: search time before each action
AI_MOVE_DELAY = 1.0   # seconds the chosen action is shown before it's played
AI_LOOKAHEAD_TURNS = 4    # turn changes to simulate before judging: 2 = to the end
                          # of the opponent's next turn
AI_EXPLORATION = 0.25     # UCT exploration constant: higher spreads the search over more
                          # alternatives. Moves usually differ by ~0.05 in value, so 1.0
                          # spread it so evenly the choice was near-random; 0.25 tested best.
AI_ACTION_COST = 0.01     # small charge per action used, so an action has to help to be
                          # chosen; stops near-pointless moves winning on noise
AI_WIDENING = 2.0         # progressive widening: a position's moves are ranked by a
AI_WIDENING_POWER = 0.5   # quick check and the search considers the best
                          # AI_WIDENING * visits ** AI_WIDENING_POWER of them
AI_PRIOR_VISITS = 2       # new moves start with this many pretend visits at their quick-check
                          # value, so an unlucky first roll (e.g. a missed 80% shot) can't bury them
AI_FOCUS_RADIUS = 2       # free steps for players within this many tiles of the ball are
                          # searched; players further away step by a quick positioning check
AI_POSITION_MIN_GAIN = 0.005  # a far player only steps if it improves the evaluation this much
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
# At full time only the score matters: the AI values a final position by
# the goals scored or conceded during its lookahead ("goal" each) plus
# AI_RESULT_WEIGHT for each step up or down from loss to draw to win.
AI_RESULT_WEIGHT = 2.0

AI_WEIGHTS = {
    "goal": 2.0,           # per goal scored (or conceded) during the lookahead
    "possession": 0.1,     # having the ball
    "loose_ball": 0.15,    # nobody has it: your nearest player closer to it than theirs
    "progress": 0.8,       # holding the ball: 0 at your own goal up to this at theirs (curved)
    "shot_chance": 0.1,    # times the shot chance from where the ball is, for its holder
    "ball_safety": 0.15,   # minus the best tackle chance opponents can set up on your carrier next turn
    "shot_threat": 0.3,    # the best shot chance your carrier can set up next turn
    "support": 0.1,        # fraction of the carrier's teammates open for a pass
    "cover": 0.1,          # defending a carrier: fraction of players goal-side of the ball
    "pressing": 0.08,      # defending a carrier: a player next to them (1) or 2 tiles away (0.5)
    "runs": 0.08,          # with the ball: fraction of teammates ahead of it and unmarked
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
