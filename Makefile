PY := .venv/bin/python

.PHONY: test demo eval serve

test:
	PYTHONPATH=. $(PY) -m pytest tests/ -q

demo:
	PYTHONPATH=. $(PY) -m workbench.cli demo

eval:
	PYTHONPATH=. $(PY) -m evals.run_eval

serve:
	PYTHONPATH=. $(PY) -m uvicorn app.main:app --reload
