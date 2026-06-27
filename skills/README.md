# Handex Skills

Handex skills are framework-level instructions that can be read by any web LLM
through the Hand Loop. A skill is any directory containing a `SKILL.md` file.

Example:

```text
skills/
  release-manager/
    SKILL.md
```

Optional front matter:

```markdown
---
name: release-manager
description: Prepare, verify, and publish release changes.
---

# Release Manager

Instructions...
```

Configure additional skill roots with:

```sh
HANDEX_SKILL_ROOTS=/opt/handex/skills:/some/other/skills
```

Handex reads skill files dynamically at runtime. It does not vendor or hard-code
Codex, OmniDoer, or any other agent's skills.
