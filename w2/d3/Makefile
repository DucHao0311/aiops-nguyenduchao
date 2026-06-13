.PHONY: install run run-prod test clean

install:
	pip install -r requirements.txt

run:
	uvicorn serve:app --host 0.0.0.0 --port 8000 --workers 1 --reload

run-prod:
	uvicorn serve:app --host 0.0.0.0 --port 8000 --workers 4 --no-access-log

test:
	pytest -v tests/

clean:
	find . -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	rm -f rca_llm_trace.log
