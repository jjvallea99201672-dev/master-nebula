# Master Nebula

Jay Vallea's portfolio site: software and apps, music, images and physics videos.
Plain HTML, CSS and JS. No build step, no dependencies.

Live: https://jjvallea99201672-dev.github.io/master-nebula/

## Layout
- `index.html`: the whole page. Sections: Software & Apps, Music, Gallery, Physics, About.
- `style.css`, `script.js`: look and behaviour (click-to-load YouTube embeds, lightbox, starfield).
- `assets/img/*.svg`: images. Each is a JPEG wrapped in a tiny SVG so it can be pushed as text. Use them in `<img>` tags as normal.
- `soul-forge/index.html`: Soul Forge Studio V3, a single self-contained HTML mastering app (no network requests).
- `lora-forge/`: prebuilt static copy of the corrected Bathurst LoRA Forge. Its training console is a **simulation**.

## Editing
- Add a YouTube video: copy a `<div class="vid" data-yt="VIDEO_ID" data-title="Title"></div>` line.
- Never commit keys, tokens, `.env` files or private data.
