# Test footage

Two clips of the same task. Put them here as `expert.mp4` and `trainee.mp4`.

## Task: reseat a RAM stick in a desktop PC

### Expert run (`expert.mp4`), done correctly, narrate each step out loud
1. Shut down the PC, switch the PSU off, unplug the power cable
2. Press the power button to drain leftover power
3. Touch bare metal on the case to ground yourself
4. Remove the side panel
5. Open the latches on the RAM slot
6. Pull the RAM stick out
7. Line up the notch on the stick with the key in the slot
8. Press down firmly until both latches click shut
9. Put the side panel back on and plug the power back in

### Trainee run (`trainee.mp4`), no narration, make these mistakes
- **Skip step 3 on camera**: ground yourself *before* you start recording, so the
  RAM is protected but the video never shows it
- **Swap the order of steps 1 and 4**: with the PC already shut down, take the side
  panel off *first*, then switch off and unplug the PSU. Always unplug before you
  touch the RAM
- **Leave one latch open** in step 8: press until only one side clicks (then fix it
  after you stop recording, before powering on)

## Filming
- Phone propped or on a tripod so it doesn't move, landscape, good light
- Frame the open case and your hands; keep the slot and latches visible
- Do each action deliberately and pause about a second after each step. Muse Spark
  sees roughly one frame per second of video (measured); the script slows clips
  down 3x before sending them, but a pause still helps
- 1–2 minutes per clip, MP4, 720p is plenty
- Nothing personal in frame (screens, mail, faces): this repo is public

## Uploading
Phone clips are usually over GitHub's 25 MB web-upload limit, so attach them to
the `test-clips` release instead (up to 2 GB per file, any format):
https://github.com/godlynot/meta/releases/new
