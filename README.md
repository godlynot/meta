# Watch Once (working title)

Film an expert doing a hands-on procedure once. Then record anyone else doing it,
and find out which steps they skipped, swapped or got wrong, with the expert's
clip showing how it should be done.

Built for the Meta Global AI Developer Hackathon (Oct 26 – Nov 4, 2026), indie
track. Everything in this repository was written during the hackathon.

## Status
Feasibility test: does Muse Spark reliably extract steps from one video and catch
mistakes in another?

```bash
pip install -r requirements.txt
export META_API_KEY=...
python scripts/feasibility.py samples/expert.mp4 samples/trainee.mp4
```

See [samples/README.md](samples/README.md) for how the test clips are filmed.
