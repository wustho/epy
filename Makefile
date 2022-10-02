.PHONY: tests
.DEFAULT_GOAL := check

check:
	mypy --follow-imports=silent src

format:
	isort src
	black src

debug:
	python -m debugpy --listen 5678 --wait-for-client -m epy_reader

dev:
	poetry install

tests:
	python -m pytest -vv

coverage:
	coverage run --include=epy.py -m pytest -vv tests
	coverage html
	python -m http.server -d htmlcov

release:
	python -m build
	twine upload --skip-existing dist/*
