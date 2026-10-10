# MGT 409 HW6: Campus Customs vendor desk agent

An agent (PydanticAI, `gpt-6-luna` through Portkey) reads one vendor document (HTML, PDF or .eml) and returns the quote facts plus a recommend / do_not_recommend decision. It is defended against hidden-text attacks with three layers (code, design, prompt).

## Set up

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then put your own Portkey key in .env
```

## Run

```bash
python agent.py --doc data/practice_docs/p01_bluehen_quote.html --setup demo
python cache_test.py        # 12 practice docs, with and without cache
python attack_test.py       # undefended agent vs the practice attacks
python defense_test.py      # each defense layer alone and all three
python run_exam.py          # the 10 exam documents, all layers on
python build_report.py      # writes output/hw6_report.html
```

`agent.py` runs all three layers by default; use `--undefended` for none or `--layers code,design,prompt` to pick.

## What's in `output/`

- `handbook_estimate.md`: token count of the handbook and its cost estimate
- `cache_test.json`, `cache_test.md`: with-cache vs no-cache runs
- `attack_results.json`, `attack_results.md`: undefended agent on the practice docs
- `defense_results.json`, `defense_results.md`: each layer alone, all three
- `defense_results_after_red_team.json`: practice docs re-run after the red-team fixes
- `red_team.md`, `red_team_*.json`: my own attacks (`data/my_attacks/`) and their results
- `exam_results.json`: final answers on the exam documents
- `hw6_report.html`: the results page (open it by double-click)
- `harness.md`: how the agent is built and the Safety section
- `usage_log.json`, `audit_trail.json`, `trap_calls.json`: per-run logs
