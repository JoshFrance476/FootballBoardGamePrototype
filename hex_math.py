"""Pure hex-grid geometry for pointy-top hexes in axial (q, r) coordinates.

Several functions are cached (functools.lru_cache): they're pure, called
constantly by the AI's search, and only ever see a few hundred inputs.

Nothing in here knows about pygame, the game rules or the config file,
so it can be reused or tested on its own.
"""
import math
from functools import lru_cache

SQRT3 = math.sqrt(3)


def rectangle_coords(cols, rows, shift_top=False):
    """Axial coordinates for a rectangular board, every other row shifted.

    In axial coordinates a straight column leans diagonally, so each pair of
    rows shifts the q range left by one to keep the board rectangular.
    The shifted rows sit half a tile to the right of the others and get one
    tile fewer: then both side edges zigzag the same way and the board is an
    exact mirror image left-to-right. Normally the second row (and every
    other row after it) is shifted; with shift_top the top row is.
    """
    coords = []
    for r in range(rows):
        start = row_start(r, shift_top)
        for q in range(start, start + cols - row_is_short(r, shift_top)):
            coords.append((q, r))
    return coords


def row_start(r, shift_top=False):
    """The q of the leftmost tile in row r of a rectangle_coords board."""
    return -((r + 1) // 2) if shift_top else -(r // 2)


def row_is_short(r, shift_top=False):
    """True for the shifted rows of a rectangle_coords board (one tile fewer)."""
    return r % 2 == (0 if shift_top else 1)


# The six neighbour offsets, clockwise on screen starting from east:
# east, south-east, south-west, west, north-west, north-east.
# A direction is stored as an index into this list.
DIRECTIONS = [(1, 0), (0, 1), (-1, 1), (-1, 0), (0, -1), (1, -1)]


@lru_cache(maxsize=None)
def rotate_offset(offset, steps):
    """Rotate an axial offset clockwise by steps * 60 degrees.

    In cube coordinates (q, r, s), one clockwise step is
    (q, r, s) -> (-r, -s, -q). With s = -q - r that gives
    q' = -r and r' = q + r.
    """
    q, r = offset
    for _ in range(steps % 6):
        q, r = -r, q + r
    return q, r


def hex_distance(a, b):
    """Number of steps between two hexes.

    With the third cube coordinate s = -q - r, the distance is the largest
    of the three coordinate differences, which equals half their sum.
    """
    dq = a[0] - b[0]
    dr = a[1] - b[1]
    return (abs(dq) + abs(dr) + abs(dq + dr)) // 2


# Players only face right or left. These are indexes into DIRECTIONS, so a
# right-facing player's "facing" neighbour is the east one, a left-facing
# player's the west one.
RIGHT, LEFT = 0, 3


def flipped(facing):
    """The opposite facing: right <-> left."""
    return LEFT if facing == RIGHT else RIGHT


@lru_cache(maxsize=None)
def mirror_offset(offset):
    """An offset reflected left-to-right: same row, opposite side.
    (q, r) -> (-q - r, r) keeps r (the row) and negates the on-screen x."""
    q, r = offset
    return (-q - r, r)


def offset_for_facing(offset, facing):
    """An action offset (written for a right-facing player) as it applies to a
    player facing `facing`: unchanged facing right, mirrored facing left."""
    return offset if facing == RIGHT else mirror_offset(offset)


def facing_after_move(start, end, current):
    """Which way a player faces after moving from start to end: the side they
    moved towards, or their current facing if the move was straight up/down."""
    dx = hex_to_pixel(*end, 1)[0] - hex_to_pixel(*start, 1)[0]
    if abs(dx) < 1e-9:
        return current
    return RIGHT if dx > 0 else LEFT


def is_in_front(facing, direction):
    """True if a neighbour in DIRECTIONS[direction] is on the side a player
    with this facing is looking at. Every hex neighbour is either to the
    right (east, north-east, south-east) or to the left."""
    to_the_right = direction in (0, 1, 5)
    return to_the_right == (facing == RIGHT)


@lru_cache(maxsize=None)
def direction_toward(a, b, prefer=None):
    """Index into DIRECTIONS that points most nearly from hex a toward hex b.

    For moves that aren't in a straight hex line, this is the nearest of the
    six directions by on-screen angle. A move exactly halfway between two
    directions picks whichever is closer to `prefer` (e.g. the current
    facing), so the player turns as little as possible.
    """
    ax, ay = hex_to_pixel(*a, 1)
    bx, by = hex_to_pixel(*b, 1)
    angle = math.degrees(math.atan2(by - ay, bx - ax)) % 360   # 0 = east, clockwise on screen

    def angle_off(direction):
        diff = abs(angle - 60 * direction) % 360
        return min(diff, 360 - diff)

    def turn_from_preferred(direction):
        if prefer is None:
            return 0
        steps = (direction - prefer) % 6
        return min(steps, 6 - steps)

    return min(range(6), key=lambda d: (round(angle_off(d), 6), turn_from_preferred(d), d))


@lru_cache(maxsize=None)
def neighbours(coord):
    """The six neighbouring coordinates, in DIRECTIONS order (a tuple)."""
    q, r = coord
    return tuple((q + dq, r + dr) for dq, dr in DIRECTIONS)


def tiles_between(a, b):
    """Hexes the straight line from the centre of a to the centre of b touches,
    not counting a and b themselves.

    "Touches" includes grazing: a line that runs exactly along the edge
    between two hexes, or clips a corner, counts both of them. Works for
    any two hexes, not just ones in a straight hex line.
    """
    return _tiles_between(tuple(a), tuple(b))


@lru_cache(maxsize=None)
def _tiles_between(a, b):
    steps = hex_distance(a, b)
    if steps <= 1:
        return frozenset()      # neighbours share an edge; nothing lies between

    # Work in a unit layout (size 1, no gap): same geometry as the screen
    start = hex_to_pixel(*a, 1)
    end = hex_to_pixel(*b, 1)

    # Candidates: the hex under each half-step point along the line, plus
    # their neighbours. Anything the line touches is within one step of one
    # of those, so this finds them all without checking the whole board.
    candidates = set()
    for i in range(2 * steps + 1):
        t = i / (2 * steps)
        point = (start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t)
        coord = pixel_to_hex(*point, 1)
        candidates.add(coord)
        candidates.update(neighbours(coord))
    candidates -= {a, b}

    return frozenset(coord for coord in candidates
                     if _segment_touches_hex(start, end, hex_to_pixel(*coord, 1), 1))


_TOUCH_TOLERANCE = 1e-6   # in hex-size units: absorbs floating-point error on exact edges


def _segment_touches_hex(p1, p2, centre, size):
    """True if segment p1-p2 touches the closed pointy-top hex (edges and corners count)."""
    if point_in_hex(p1, centre, size + _TOUCH_TOLERANCE):
        return True
    corners = hex_corners(centre, size)
    for i in range(6):
        if _segments_distance(p1, p2, corners[i], corners[(i + 1) % 6]) <= _TOUCH_TOLERANCE:
            return True
    return False


def _cross(o, a, b):
    """Positive if o->a->b turns anticlockwise, negative if clockwise, 0 if in line."""
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _point_segment_distance(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length_sq = dx * dx + dy * dy
    t = 0.0 if length_sq == 0 else ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length_sq
    t = max(0.0, min(1.0, t))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def _segments_distance(p1, p2, q1, q2):
    """Shortest distance between segments p1-p2 and q1-q2 (0 if they cross)."""
    if (_cross(q1, q2, p1) * _cross(q1, q2, p2) < 0
            and _cross(p1, p2, q1) * _cross(p1, p2, q2) < 0):
        return 0.0
    return min(_point_segment_distance(p1, q1, q2), _point_segment_distance(p2, q1, q2),
               _point_segment_distance(q1, p1, p2), _point_segment_distance(q2, p1, p2))


def hex_to_pixel(q, r, size, gap=0, origin=(0, 0)):
    """Pixel centre of the pointy-top hex at axial coordinate (q, r).

    The gap is applied by laying hexes out as if they were slightly larger
    than they're drawn. Neighbouring centres are sqrt(3) * spacing apart, so
    adding gap / sqrt(3) to the size opens up exactly `gap` pixels between
    every pair of touching edges.
    """
    spacing = size + gap / SQRT3
    x = origin[0] + spacing * SQRT3 * (q + r / 2)
    y = origin[1] + spacing * 1.5 * r
    return x, y


def pixel_to_hex(x, y, size, gap=0, origin=(0, 0)):
    """Axial coordinate (q, r) of the hex nearest to pixel (x, y).

    This is hex_to_pixel run backwards. It gives fractional coordinates,
    which hex_round snaps to the nearest whole hex.
    """
    spacing = size + gap / SQRT3
    px = (x - origin[0]) / spacing
    py = (y - origin[1]) / spacing
    q = SQRT3 / 3 * px - py / 3
    r = 2 / 3 * py
    return hex_round(q, r)


def hex_round(q, r):
    """Round fractional axial coordinates to the nearest hex.

    Rounding q and r separately picks the wrong hex near some edges. Using
    the third cube coordinate s (q + r + s == 0): round all three, then
    rebuild whichever one moved the most from the other two.
    """
    s = -q - r
    rq, rr, rs = round(q), round(r), round(s)
    dq, dr, ds = abs(rq - q), abs(rr - r), abs(rs - s)
    if dq > dr and dq > ds:
        rq = -rr - rs
    elif dr > ds:
        rr = -rq - rs
    return rq, rr


def point_in_hex(point, centre, size):
    """True if point is inside the drawn pointy-top hex (not in the gap)."""
    dx = abs(point[0] - centre[0])
    dy = abs(point[1] - centre[1])
    return dx <= SQRT3 / 2 * size and dy <= size - dx / SQRT3


def hex_corners(centre, size):
    """The six corner points of a pointy-top hex."""
    cx, cy = centre
    points = []
    for i in range(6):
        angle = math.radians(60 * i - 30)
        points.append((cx + size * math.cos(angle), cy + size * math.sin(angle)))
    return points
