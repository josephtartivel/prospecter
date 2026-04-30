# Prompts

System prompts for each agent live here as `{agent}_v{n}.md`. Versioning is
explicit so we can diff iterations and ablate by prompt version in the eval.

## Convention

- Filename: `{agent}_v{n}.md`. `n` starts at 1, increments per breaking
  change (anything that would shift eval numbers).
- The file is the *raw* system prompt. Placeholders use `{NAME}` syntax;
  `prompt_library.py` substitutes at load time.
- The first line is a short top-of-file comment (`<!-- … -->`) describing
  what changed vs the previous version. Required for v2+.

## Files

- `icp_parser_v1.md` — first parser prompt. Grounds the model in NAF code
  classes, INSEE region codes, and SIRENE headcount tranches.
- `scorer_v1.md` — first scorer rubric. Four-axis scoring with explicit
  cap-and-rationale rules.

## Anti-patterns we don't do

- **Embedding the prompt in Python**. f-strings inline get edited
  invisibly; we want every prompt change to show up as a clear git diff.
- **Bullet-list "you are a helpful assistant" preambles**. These cost
  tokens, signal AI-template, and don't change behaviour on serious models.
- **Telling the model what's at stake** ("this is critical", "be very
  careful"). Use the rubric to enforce care; threats don't help.
