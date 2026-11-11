PY := .venv/bin/python
UV ?= $(shell command -v uv || echo $(HOME)/.local/bin/uv)
STAMP := .venv/.installed

.PHONY: all setup fetch load resolve warehouse evaluate extract bench report rescore test clean

# One command, end to end. Downloads are cached in data/raw and model answers in data/cache,
# so a second run makes no new requests to ClinicalTrials.gov and no new model calls.
all: setup fetch load resolve warehouse evaluate extract bench report test

$(PY):
	$(UV) venv --python 3.12 .venv

$(STAMP): requirements.txt | $(PY)
	$(UV) pip install --python $(PY) -r requirements.txt
	@touch $(STAMP)

setup: $(STAMP)

fetch load resolve warehouse evaluate extract bench report rescore: $(STAMP)
	$(PY) -m readout $@

# Trial site locations for Readout Globe, after `make warehouse`: download (cached in data/raw/ctgov_sites),
# load raw.ct_sites, then build and test only the site models (marts.fct_trial_sites, marts.dim_site_location).
.PHONY: sites
sites: $(STAMP)
	$(PY) -m readout sites-fetch
	$(PY) -m readout sites-load
	$(PY) -m readout sites-build

test: $(STAMP)
	$(PY) -m pytest -q

clean:
	rm -rf warehouse/target warehouse/logs .pytest_cache
