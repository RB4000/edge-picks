PY := .venv/bin/python

.PHONY: setup update build backtest sheet deploy publish serve clean

setup:            ## create virtualenv (Python 3.11-3.13) and install deps
	python3.13 -m venv .venv || python3 -m venv .venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -r requirements.txt

update:           ## refresh data, capture lines, lock picks, regrade, rebuild site
	./update.sh

build:            ## rebuild ./site from the ledger without refreshing data
	$(PY) -m nfledge.pipeline build

backtest:         ## walk-forward backtest -> backtest_results.md
	$(PY) -m nfledge.pipeline backtest

sheet:            ## print this week's pick sheet
	$(PY) -m nfledge.pipeline sheet

deploy:           ## push ./site to Cloudflare Pages
	./deploy.sh

publish: update deploy

serve:            ## preview the site at http://localhost:8000
	$(PY) -m http.server 8000 --directory site

clean:            ## remove caches and built site (never touches ./ledger)
	rm -rf site site.tmp data/cache
