from PIL import Image, ImageDraw
import os

os.makedirs("reference", exist_ok=True)
os.makedirs("live", exist_ok=True)

def make_screen(filename, header_text, title, subtitle, button_label, status_text):
    img = Image.new("RGB", (800, 600), color=(245, 245, 245))
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, 800, 60], fill=(30, 30, 90))
    draw.text((20, 18), header_text, fill=(255, 255, 255))

    draw.rectangle([50, 100, 550, 300], fill=(200, 220, 255), outline=(100, 100, 200), width=2)
    draw.text((65, 120), title, fill=(30, 30, 90))
    draw.text((65, 150), subtitle, fill=(80, 80, 80))

    draw.rectangle([50, 320, 200, 355], fill=(200, 220, 255))
    draw.text((60, 330), status_text, fill=(30, 30, 90))

    draw.rectangle([50, 400, 200, 440], fill=(0, 120, 200))
    draw.text((75, 415), button_label, fill=(255, 255, 255))

    img.save(filename)
    print(f"Saved: {filename}")

make_screen(
    "reference/screen.png",
    header_text="MyApp v1 — Intended",
    title="Welcome, Admin",
    subtitle="You have 3 pending approvals.",
    button_label="Approve All",
    status_text="Status: Active"
)

make_screen(
    "live/screen.png",
    header_text="MyApp v1 — Deployed",
    title="Welcome, Admin",
    subtitle="You have 0 pending approvals.",
    button_label="Submit",
    status_text="Status: Inactive"
)