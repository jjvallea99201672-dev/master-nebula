"""
Nebula Bloom — made from the snowflake formula, mutated.

Snowflake:  forward, split at 45°, scale by 0.7, repeat (perfect symmetry)
Nebula Bloom: forward, split at a *varying* angle, scale by ~0.68,
              add a little curl at each level, repeat — and let the
              colour travel from indigo -> cyan -> violet -> pink as
              you go out. Same rule, completely different creature.
"""
import math
import random
from PIL import Image, ImageDraw, ImageFilter

random.seed(11)  # peoplelie11
W = 2000
img = Image.new("RGB", (W, W), (7, 8, 22))
glow = Image.new("RGB", (W, W), (0, 0, 0))
draw = ImageDraw.Draw(img)
gdraw = ImageDraw.Draw(glow)

# faint background stars
for _ in range(350):
    x, y = random.randrange(W), random.randrange(W)
    r = random.choice([1, 1, 1, 2])
    draw.ellipse([x - r, y - r, x + r, y + r], fill=(140, 160, 210))

PALETTE = [
    (80, 90, 255),    # indigo (trunk)
    (60, 180, 255),   # blue
    (60, 230, 220),   # cyan
    (160, 110, 255),  # violet
    (255, 110, 200),  # pink
    (255, 190, 120),  # gold tips
]

def colour(depth_left, max_depth=7):
    idx = min(len(PALETTE) - 1, max_depth - depth_left)
    return PALETTE[idx]

tips = []

def branch(x, y, angle, length, depth, curl):
    if depth == 0 or length < 3:
        tips.append((x, y))
        return
    rad = math.radians(angle)
    x2 = x + math.cos(rad) * length
    y2 = y + math.sin(rad) * length
    c = colour(depth)
    w = max(1, depth)
    gdraw.line([(x, y), (x2, y2)], fill=tuple(v // 3 for v in c), width=w * 4)
    draw.line([(x, y), (x2, y2)], fill=c, width=w)

    # the mutations: uneven split, uneven shrink, and a curl that
    # makes the whole thing spiral instead of mirroring
    split = random.uniform(24, 52)
    branch(x2, y2, angle - split + curl, length * random.uniform(0.60, 0.72), depth - 1, curl)
    branch(x2, y2, angle + split + curl, length * random.uniform(0.60, 0.72), depth - 1, curl)
    # occasional third, middle shoot — snowflakes never do this
    if depth > 3 and random.random() < 0.35:
        branch(x2, y2, angle + curl * 2, length * 0.55, depth - 2, curl)

cx = cy = W // 2
# 9 uneven arms (snowflake insists on 6) rotated by the golden angle-ish
for i in range(9):
    base = -90 + i * 40 + random.uniform(-9, 9)
    branch(cx, cy, base, 205, 7, curl=7)

img = Image.blend(img, img.filter(ImageFilter.GaussianBlur(0.4)), 0.85)
# composite the glow underneath the sharp lines
base = Image.new("RGB", (W, W), (7, 8, 22))
glow_blur = glow.filter(ImageFilter.GaussianBlur(9))
img = Image.blend(glow_blur, img, 0.72)
draw = ImageDraw.Draw(img)
# star-like sparks at every branch tip
for (x, y) in tips:
    r = random.uniform(2, 4.5)
    draw.ellipse([x - r, y - r, x + r, y + r], fill=(255, 235, 200))

img.save("nebula_bloom.png")
print(f"done — {len(tips)} tips")
