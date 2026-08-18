# Jingcai Stage3 Canonical Skill v1.0.0

This bundle consolidates the currently available Stage3 code, governance, and Reality-First Research OS into one Skill-oriented package.

Start with `SKILL.md`.

Validation:
```bash
python runtime/validate_skill.py
```

External target coverage audit example:
```bash
python runtime/external_coverage_gate.py examples/targets_001_004.example.json /path/to/theodds_stage3.json
```

Important: this package consolidates orchestration and verified source assets. It does not grant 3.5 Alpha production promotion or freeze new rating thresholds.
