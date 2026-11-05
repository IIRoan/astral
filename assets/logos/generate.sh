#!/usr/bin/env bash
# Regenerates the logo variants from logo-astral.svg. Run it after changing the logo.
# Needs rsvg-convert and ImageMagick.
set -euo pipefail
cd "$(dirname "$0")"

dark='#141b2d'

# Light backgrounds (README in light mode): the white ring, outline and sparkle turn dark.
sed "s/#ffffff/$dark/g" logo-astral.svg > logo-astral-light.svg

# Icons shown on any background (ArtifactHub, start-here, distro logo fallbacks):
# the logo on a dark rounded square.
sed -e "s|</defs>|</defs>\n  <rect width=\"120\" height=\"120\" rx=\"26\" fill=\"$dark\"/>\n  <g transform=\"translate(60 60) scale(0.86) translate(-60 -60)\">|" \
    -e 's|</svg>|  </g>\n</svg>|' logo-astral.svg > logo-astral-badge.svg

# Raster stand-ins for the files generic-logos ships (same sizes as the originals).
rsvg-convert -w 240 -h 240 logo-astral.svg |
    magick - -background none -gravity center -extent 240x310 -strip png:logo-astral-240x310.png
rsvg-convert -w 240 -h 240 logo-astral-badge.svg |
    magick - -background none -gravity center -extent 240x310 -strip png:logo-astral-badge-240x310.png
rsvg-convert -w 128 -h 128 logo-astral-badge.svg | magick - -strip png:logo-astral-badge-128.png

# mkosi copies file modes into the image; keep them world-readable whatever the umask.
chmod 0644 logo-astral-*.svg logo-astral-*.png
