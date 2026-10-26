ROWS ?= 2000000
PY = .venv/bin/python

.PHONY: setup db data close run test demo clean

setup:            ## create virtualenv and install dependencies
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt
	[ -f .env ] || cp .env.example .env

db:               ## install and start Exasol Personal locally
	exasol install local
	exasol slc install PYTHON3 --auto-approve
	exasol info

data:             ## generate synthetic bank data and load it into Exasol
	$(PY) -m closeloop.load --rows $(ROWS)

close:            ## run the month-end close from the command line
	$(PY) -m closeloop.pipeline

test:             ## end-to-end tests against Exasol (isolated CLOSELOOP_TEST schema)
	$(PY) -m pytest tests -q

run:              ## start the dashboard
	.venv/bin/streamlit run app.py

demo: data run

clean:
	rm -rf .venv
