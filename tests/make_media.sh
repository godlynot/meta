#!/bin/sh
# Builds the test videos in tests/media: a synthetic "pack the tray" procedure,
# the same attempt as an HEVC .mov and as a rotated phone video, a non-video,
# an HLS playlist pointing at /etc/passwd, and clips for the size/length limits.
set -e
cd "$(dirname "$0")" && mkdir -p media
python3 make_synth.py media
ffmpeg -loglevel error -y -i media/trainee.mp4 -f lavfi -i sine=frequency=300 -shortest -c:v libx265 -tag:v hvc1 -c:a aac media/phone_hevc.mov
ffmpeg -loglevel error -y -display_rotation 90 -i media/trainee.mp4 -c copy media/rotated.mp4
printf 'this is not a video\n' > media/notvideo.mp4
printf '#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:1.0,\n/etc/passwd\n#EXT-X-ENDLIST\n' > media/playlist.bin
ffmpeg -loglevel error -y -f lavfi -i testsrc2=size=640x360:rate=15 -t 8 -pix_fmt yuv420p -c:v libx264 media/eightsec.mp4
ffmpeg -loglevel error -y -f lavfi -i "nullsrc=s=640x360:r=15,geq=random(1)*255:128:128" -t 3 -c:v libx264 -crf 0 media/big.mp4
# Fake webcam feed for the live test (the flawed attempt, looped by Chromium)
ffmpeg -loglevel error -y -i media/trainee.mp4 -vf scale=640:360,fps=15 -pix_fmt yuv420p media/trainee.y4m
