# Competition Discovery Workflow

## Requirements
- Python 3.11+
- `pip install -r requirements.txt`
- Chrome/ChromeDriver already required for the existing Selenium tooling

## Quick start
```bash
# (optional) create an isolated environment
python3 -m venv .comp-scraper-venv
. .comp-scraper-venv/bin/activate

pip install -r requirements.txt
python competition_discovery.py -o competition_entries.xlsx
```

The script exports an Excel workbook containing the combined listings from:
- competitions-time.co.uk
- theprizefinder.com

Blocked sources are logged during the run so you can decide whether to supply
manual cookies/sessions at a later stage.

## Columns captured
- `Source` — origin website
- `Title` — headline from the feed
- `Prize` — currently mirrors the title text
- `Link` — “enter competition” URL (internal tracking URLs where applicable)
- `Closing Date (ISO)` — parsed date when available
- `Closing Date (Raw)` — original text (useful if parsing fails)
- `Successful Submission` — placeholder for a future automation workflow
- `Is New This Run` — `YES` when the link has not been seen in previous runs (tracked via `competition_state.json`)

The script keeps a lightweight JSON state file (`competition_state.json` by default) so each run flags which competitions are freshly discovered. Override the location with `--state path/to/state.json` if you’d like to store it elsewhere or maintain multiple trackers. The state now tracks both `seen` and `submitted` links.

Add `--summary reports/summary.txt` to also generate a plain-text digest with totals, new counts, and upcoming closing deadlines. Pair it with `--summary-webhook https://hooks.slack.com/...` to POST the digest to a chat webhook, or `--summary-email alice@example.com bob@example.com` to send it via SMTP (configure `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM` in the environment).

## Scheduling
Once you’re happy with the output, add a weekly cron/launchd task that activates
the virtual environment and runs `competition_discovery.py`. Point the `-o`
argument at a dated path (for example `reports/$(date +%Y-%m-%d).xlsx`) so each
run is archived.

Entries that you have already submitted (tracked in the state file) are automatically suppressed from future spreadsheets, keeping the focus on fresh opportunities.

Instagram and TikTok competitions are filtered out of the export by default.

## Automated entry pilot
Use `auto_entry_runner.py` to process the spreadsheet and launch Selenium for
pre-approved targets:
```bash
python auto_entry_runner.py \
  --entries competition_entries.xlsx \
  --targets automation_targets.json \
  --state competition_state.json \
  --dry-run
```

Populate `automation_targets.json` with one entry per safe URL substring, plus
the config/data files to use (defaults live under `automation_configs/`). Sample entries are provided for
`theprizefinder.com/link-track` redirects and `competitions-time.co.uk/redir/` URLs, each pointing at their own
data/config pairs. Remove `--dry-run` and add `--auto-confirm` once
you’re happy with the preview prompt. Successful submissions are written back
to the `Successful Submission` column in the workbook and recorded in the state file so the discovery run stops surfacing them.

If Selenium reports it "cannot obtain driver for chrome", either install ChromeDriver manually and set
`"webdriver_path": "/path/to/chromedriver"` in the relevant config JSON or install `webdriver-manager`
(`pip install webdriver-manager`) so the tool can download a matching driver automatically.

## Roadmap
1. Add authenticated fetchers for Latest Deals, MyOffers, Loquax, and Woman Magazine.
2. Normalise prize and category data so multiple sources can be merged cleanly.
3. Feed the exported workbook into the existing Selenium autofill workflow
   once the per-site entry logic is available and compliant with the relevant T&Cs.
