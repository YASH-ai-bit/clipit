"""
make_gif.py — Generates a sleek, animated monochrome GIF from clip-svgrepo-com.svg.
"""

import io
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter
from resvg_py import svg_to_bytes

def create_animated_logo():
    svg_path = Path("clip-svgrepo-com.svg")
    output_gif_root = Path("logo.gif")
    output_gif_static = Path("static/logo.gif")

    svg_content = svg_path.read_text(encoding="utf-8")
    
    # Replace fill with white for dark monochrome mode
    svg_white = svg_content.replace('fill="#000000"', 'fill="#FFFFFF"').replace("fill='currentColor'", 'fill="#FFFFFF"')

    # Rasterize SVG to high-res PNG bytes
    png_bytes = svg_to_bytes(svg_white, width=400, height=400)
    base_vector = Image.open(io.BytesIO(png_bytes)).convert("RGBA")

    # Resize vector to 220x220
    vector_img = base_vector.resize((220, 220), Image.Resampling.LANCZOS)

    # Generate 36 frames (smooth loop)
    num_frames = 36
    frames = []
    width, height = 360, 360

    for i in range(num_frames):
        theta = (2 * math.pi * i) / num_frames
        
        # Motion calculations
        float_y = math.sin(theta) * 14.0       # Gentle vertical bobbing
        rot_angle = math.sin(theta) * 5.0      # Slight organic tilt
        glow_alpha = int(45 + 30 * math.cos(theta))  # Pulsing ambient glow
        halo_radius = int(85 + 15 * math.sin(theta)) # Breathing aura

        # Base dark canvas (#050505)
        canvas = Image.new("RGBA", (width, height), (5, 5, 5, 255))

        # Ambient radial aura
        glow_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow_layer)
        glow_center = (width // 2, int(height // 2 + float_y))
        
        glow_draw.ellipse(
            (glow_center[0] - halo_radius, glow_center[1] - halo_radius,
             glow_center[0] + halo_radius, glow_center[1] + halo_radius),
            fill=(255, 255, 255, glow_alpha)
        )
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=32))
        canvas.alpha_composite(glow_layer)

        # Rotate and position the clip vector
        rotated_vec = vector_img.rotate(rot_angle, resample=Image.Resampling.BICUBIC, expand=True)
        vec_x = (width - rotated_vec.width) // 2
        vec_y = int((height - rotated_vec.height) // 2 + float_y)

        canvas.alpha_composite(rotated_vec, (vec_x, vec_y))

        # Minimalist pedestal pulse line at bottom
        draw = ImageDraw.Draw(canvas)
        line_w = int(50 + 20 * math.sin(theta))
        line_y = height - 32
        line_alpha = int(60 + 40 * math.sin(theta))
        draw.line(
            [(width // 2 - line_w, line_y), (width // 2 + line_w, line_y)],
            fill=(255, 255, 255, line_alpha),
            width=2
        )

        frames.append(canvas.convert("P", palette=Image.Palette.ADAPTIVE))

    # Save animated GIF (approx 30fps)
    frame_duration = 33  # ~30 fps
    frames[0].save(
        output_gif_root,
        save_all=True,
        append_images=frames[1:],
        duration=frame_duration,
        loop=0,
        optimize=True,
    )

    output_gif_static.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        output_gif_static,
        save_all=True,
        append_images=frames[1:],
        duration=frame_duration,
        loop=0,
        optimize=True,
    )

    print(f"[+] Animated logo GIF successfully generated:")
    print(f"  - {output_gif_root.resolve()}")
    print(f"  - {output_gif_static.resolve()}")

if __name__ == "__main__":
    create_animated_logo()
