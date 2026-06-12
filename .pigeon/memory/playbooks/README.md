# Playbooks — procedural memory

One Markdown file per routine: how releases are cut, how spatial data is
validated, how a survey is processed. Written by humans or promoted from
distilled sessions; agents find them via `pigeon retrieve --scope memory`
and `pigeon pack`. Keep each playbook short, imperative, and current —
this directory is committed and shared between humans and agents.

Pages that declare YAML frontmatter become *projectable skills*: `pigeon
refresh` generates each runtime's native subagent file from them (Claude
Code: `.claude/agents/<name>.md`), so a `crew:` entry's `skill:` name
resolves to the same canonical page on every CLI:

    ---
    name: security-audit
    description: Adversarial security review of changed code.
    ---
    You are a security reviewer for this repository. ...
