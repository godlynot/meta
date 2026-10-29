"""Feasibility test: can Muse Spark learn a procedure from one expert video
and catch mistakes in a second attempt?

Usage:
    python scripts/feasibility.py samples/expert.mp4 samples/trainee.mp4

Writes the extracted procedure and the audit to out/ and prints a report.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from openai import OpenAI

MODEL = "muse-spark-1.3"
# Standard-mode prices, USD per 1M tokens (dev.meta.ai/docs/pricing-rate-limits).
PRICE_IN, PRICE_OUT = 1.25, 4.25

PROCEDURE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["task", "steps"],
    "properties": {
        "task": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "title", "detail", "start_s", "end_s", "objects", "critical"],
                "properties": {
                    "id": {"type": "integer"},
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "start_s": {"type": "number"},
                    "end_s": {"type": "number"},
                    "objects": {"type": "array", "items": {"type": "string"}},
                    "critical": {"type": "boolean"},
                },
            },
        },
    },
}

AUDIT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["steps", "extra_actions"],
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "status", "start_s", "end_s", "evidence", "fix"],
                "properties": {
                    "id": {"type": "integer"},
                    "status": {
                        "type": "string",
                        "enum": ["done", "skipped", "out_of_order", "incorrect", "uncertain"],
                    },
                    "start_s": {"type": ["number", "null"]},
                    "end_s": {"type": ["number", "null"]},
                    "evidence": {"type": "string"},
                    "fix": {"type": "string"},
                },
            },
        },
        "extra_actions": {"type": "array", "items": {"type": "string"}},
    },
}

EXTRACT_PROMPT = """You are watching an expert perform a hands-on procedure while narrating it.
Break the procedure into its distinct, ordered steps. A step is one physical action with a
visible outcome (e.g. "unplug the power cable", "press the module until both latches click").
For each step give the time range in seconds where it happens, the physical objects involved
(short noun phrases a segmentation model could find, e.g. "RAM stick", "slot latch"), and
whether skipping it would cause damage, danger or failure (critical).
Use the narration to understand intent, but only include steps you can see being performed."""

AUDIT_PROMPT = """You are checking a trainee's attempt at a procedure against the expert's
reference steps below. Watch the trainee video and, for every reference step, decide:
- done: performed correctly and in order
- skipped: never performed
- out_of_order: performed, but before a step that should precede it
- incorrect: attempted but done wrongly (wrong part, wrong place, not completed)
- uncertain: cannot tell from the footage (say why in evidence)
Give the time range in the trainee video where you saw it (null if skipped), the visual
evidence for your decision, and a one-sentence fix for anything not "done".
Also list any actions the trainee took that are not in the reference procedure.
Be strict: only mark "done" if you actually see it happen.

Reference procedure:
{procedure}"""


def upload(client, path):
    with open(path, "rb") as f:
        return client.files.create(file=f, purpose="user_data").id


def ask(client, file_id, prompt, schema, name):
    r = client.responses.create(
        model=MODEL,
        max_output_tokens=16000,
        input=[{
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_file", "file_id": file_id},
            ],
        }],
        text={"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}},
    )
    return json.loads(r.output_text), r.usage


def fmt_t(s):
    return "  -  " if s is None else f"{int(s // 60)}:{s % 60:04.1f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("expert")
    ap.add_argument("trainee")
    ap.add_argument("--out", default="out")
    args = ap.parse_args()

    key = os.environ.get("META_API_KEY")
    if not key:
        sys.exit("META_API_KEY is not set")
    client = OpenAI(base_url="https://api.meta.ai/v1", api_key=key)
    out = Path(args.out)
    out.mkdir(exist_ok=True)

    tokens_in = tokens_out = 0
    expert_id, trainee_id = upload(client, args.expert), upload(client, args.trainee)
    try:
        procedure, usage = ask(client, expert_id, EXTRACT_PROMPT, PROCEDURE_SCHEMA, "procedure")
        tokens_in, tokens_out = usage.input_tokens, usage.output_tokens
        (out / "procedure.json").write_text(json.dumps(procedure, indent=2))

        print(f"\nPROCEDURE: {procedure['task']}")
        for s in procedure["steps"]:
            flag = " [critical]" if s["critical"] else ""
            print(f"  {s['id']:>2}. {fmt_t(s['start_s'])}  {s['title']}{flag}")

        prompt = AUDIT_PROMPT.format(procedure=json.dumps(procedure["steps"], indent=2))
        audit, usage = ask(client, trainee_id, prompt, AUDIT_SCHEMA, "audit")
        tokens_in += usage.input_tokens
        tokens_out += usage.output_tokens
        (out / "audit.json").write_text(json.dumps(audit, indent=2))
    finally:
        client.files.delete(expert_id)
        client.files.delete(trainee_id)

    titles = {s["id"]: s["title"] for s in procedure["steps"]}
    print("\nAUDIT")
    for a in audit["steps"]:
        mark = "OK " if a["status"] == "done" else "XX "
        print(f"  {mark}{a['id']:>2}. {fmt_t(a['start_s'])}  {a['status']:<12} {titles.get(a['id'], '?')}")
        if a["status"] != "done":
            print(f"        evidence: {a['evidence']}")
            print(f"        fix:      {a['fix']}")
    for x in audit["extra_actions"]:
        print(f"  ++ extra: {x}")

    cost = tokens_in / 1e6 * PRICE_IN + tokens_out / 1e6 * PRICE_OUT
    print(f"\ntokens in={tokens_in} out={tokens_out}  cost≈${cost:.3f}")


if __name__ == "__main__":
    main()
